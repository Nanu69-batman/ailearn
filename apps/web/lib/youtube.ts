/**
 * YouTube URL helpers (client-side validation for the course ingest hub).
 * Accepted forms: youtube.com/watch?v=…, youtu.be/…, youtube.com/shorts/…
 * (plus /embed/ and /live/ variants, and a bare 11-char video ID).
 */

const VIDEO_ID = "[A-Za-z0-9_-]{11}";

const URL_PATTERNS: RegExp[] = [
  new RegExp(`youtube\\.com/watch\\?(?:[^#]*[?&])?v=(${VIDEO_ID})`),
  new RegExp(`youtube\\.com/shorts/(${VIDEO_ID})`),
  new RegExp(`youtu\\.be/(${VIDEO_ID})`),
  new RegExp(`youtube\\.com/embed/(${VIDEO_ID})`),
  new RegExp(`youtube\\.com/live/(${VIDEO_ID})`),
];

const BARE_ID = /^[A-Za-z0-9_-]{11}$/;

/** Extract the 11-char video ID, or null when the input is not a valid URL. */
export function extractYouTubeId(input: string): string | null {
  const value = input.trim();
  if (!value) return null;
  if (BARE_ID.test(value)) return value;
  for (const pattern of URL_PATTERNS) {
    const match = value.match(pattern);
    if (match) return match[1];
  }
  return null;
}

/** True when the input is an accepted YouTube watch/shorts/share URL (or ID). */
export function isYouTubeUrl(input: string): boolean {
  return extractYouTubeId(input) !== null;
}

export const YOUTUBE_URL_HINT =
  "Use a youtube.com/watch?v=…, youtu.be/…, or youtube.com/shorts/… link.";

/** Privacy-enhanced embed base for the nocookie player. */
export function youTubeNoCookieEmbedUrl(youtubeId: string): string {
  return `https://www.youtube-nocookie.com/embed/${youtubeId}`;
}
