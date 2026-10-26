import { getAccessToken } from "@/lib/supabase/client";

/**
 * Browser side of live voice (docs/VOICE_PROTOCOL.md): the socket URLs, the
 * model list, microphone capture (16 kHz mono PCM16) and 24 kHz playback.
 * The Gemini key never reaches the browser; the API relays everything.
 */

export interface VoiceModel {
  key: string;
  label: string;
  model: string;
  thinking: boolean;
}

export interface VoiceModels {
  enabled: boolean;
  dialog: VoiceModel[];
  transcribe: { model: string };
  /** Prebuilt voices and speaking tones; absent from an older API. */
  voices?: Array<{ name: string; description: string }>;
  default_voice?: string;
  tones?: Array<{ key: string; label: string }>;
  default_tone?: string;
}

function baseUrl(): string {
  return (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/$/, "");
}

/** `ws(s)://…/voice/<name>` from NEXT_PUBLIC_API_URL; null when the API is not configured. */
export function voiceWsUrl(name: "live" | "transcribe"): string | null {
  const base = baseUrl();
  if (!base) return null;
  return `${base.replace(/^http/i, "ws")}/voice/${name}`;
}

/** The Supabase access token, or null (signed out / Supabase not configured). */
export async function voiceAccessToken(): Promise<string | null> {
  return getAccessToken();
}

/** null: no API configured, unreachable, or voice not enabled. The UI hides voice. */
export async function fetchVoiceModels(): Promise<VoiceModels | null> {
  const base = baseUrl();
  if (!base) return null;
  try {
    const res = await fetch(`${base}/voice/models`);
    if (!res.ok) return null;
    const body = (await res.json()) as VoiceModels;
    return body.enabled && Array.isArray(body.dialog) ? body : null;
  } catch {
    return null;
  }
}

/** Learner-facing text for a failed getUserMedia / AudioWorklet setup. */
export function micErrorMessage(err: unknown): string {
  const name = err instanceof DOMException ? err.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "Microphone access was denied. Allow the microphone in your browser's site settings and try again.";
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") return "No microphone was found.";
  if (name === "NotReadableError") return "The microphone is in use by another app.";
  return err instanceof Error && err.message ? err.message : "Could not start the microphone.";
}

/** Join dictated text onto what is already typed, with one space between. */
export function joinText(base: string, add: string): string {
  const t = add.trim();
  if (!t) return base;
  return base && !/\s$/.test(base) ? `${base} ${t}` : `${base}${t}`;
}

/** What the textarea shows while dictating: committed text, then the live interim. */
export function withInterim(base: string, interim: string): string {
  return interim ? joinText(base, interim) : base;
}

/** The textarea changed: drop the interim suffix again so it is never committed as typed text. */
export function stripInterim(shown: string, base: string, interim: string): string {
  return interim && shown === withInterim(base, interim) ? base : shown;
}

// The worklet runs on the audio thread: box-filter downsample to 16 kHz, PCM16,
// 1600 samples (100 ms) per message.
const WORKLET_SRC = `
class Pcm16kProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.out = new Int16Array(1600);
    this.n = 0; this.sum = 0; this.cnt = 0; this.i = 0; this.next = this.ratio;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let k = 0; k < ch.length; k++) {
      this.sum += ch[k]; this.cnt++; this.i++;
      if (this.i >= this.next) {
        const v = Math.max(-1, Math.min(1, this.sum / this.cnt));
        this.out[this.n++] = v < 0 ? v * 32768 : v * 32767;
        this.sum = 0; this.cnt = 0; this.next += this.ratio;
        if (this.n === 1600) {
          this.port.postMessage(this.out.buffer, [this.out.buffer]);
          this.out = new Int16Array(1600);
          this.n = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("pcm16k", Pcm16kProcessor);
`;

export interface MicCapture {
  stop: () => void;
}

/** Streams the microphone as ~100 ms PCM16 mono 16 kHz chunks. Throws a DOMException when denied. */
export async function startMicCapture(onChunk: (pcm: ArrayBuffer) => void): Promise<MicCapture> {
  if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
    throw new Error("This browser cannot record audio.");
  }
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  let ctx: AudioContext | null = null;
  try {
    ctx = new AudioContext();
    if (!ctx.audioWorklet) throw new Error("This browser cannot process audio live.");
    const url = URL.createObjectURL(new Blob([WORKLET_SRC], { type: "application/javascript" }));
    try {
      await ctx.audioWorklet.addModule(url);
    } finally {
      URL.revokeObjectURL(url);
    }
    if (ctx.state === "suspended") await ctx.resume();
    const source = ctx.createMediaStreamSource(stream);
    const node = new AudioWorkletNode(ctx, "pcm16k", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
    node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => onChunk(e.data);
    // Silent sink: some browsers only pull a worklet that reaches the destination.
    const mute = ctx.createGain();
    mute.gain.value = 0;
    source.connect(node);
    node.connect(mute);
    mute.connect(ctx.destination);
    const audioCtx = ctx;
    return {
      stop: () => {
        node.port.onmessage = null;
        try {
          source.disconnect();
          node.disconnect();
        } catch {
          // already disconnected
        }
        stream.getTracks().forEach((t) => t.stop());
        void audioCtx.close().catch(() => {});
      },
    };
  } catch (err) {
    stream.getTracks().forEach((t) => t.stop());
    if (ctx) void ctx.close().catch(() => {});
    throw err;
  }
}

/** Plays 24 kHz PCM16 mono frames back to back; flush() drops what is queued (barge-in). */
export class PcmPlayer {
  private ctx: AudioContext;
  private next = 0;
  private sources = new Set<AudioBufferSourceNode>();

  constructor() {
    this.ctx = new AudioContext({ sampleRate: 24000 });
    void this.ctx.resume().catch(() => {});
  }

  enqueue(data: ArrayBuffer): void {
    const n = data.byteLength >> 1;
    if (n === 0 || this.ctx.state === "closed") return;
    const view = new DataView(data);
    const buf = this.ctx.createBuffer(1, n, 24000);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < n; i++) ch[i] = view.getInt16(i * 2, true) / 32768;
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.ctx.destination);
    const at = Math.max(this.ctx.currentTime + 0.03, this.next);
    src.start(at);
    this.next = at + buf.duration;
    this.sources.add(src);
    src.onended = () => this.sources.delete(src);
  }

  flush(): void {
    for (const s of this.sources) {
      try {
        s.stop();
      } catch {
        // already ended
      }
    }
    this.sources.clear();
    this.next = 0;
  }

  close(): void {
    this.flush();
    void this.ctx.close().catch(() => {});
  }
}
