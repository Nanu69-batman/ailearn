/**
 * Local dev fixtures used when the backend is unreachable.
 * Shapes mirror the API contract in lib/api.ts so swapping
 * to the live backend requires no component changes.
 */
import type {
  ChatMode,
  ChatMessage,
  ChatResponse,
  LmsSessionRequest,
  LmsSessionResponse,
} from "./api";

export function devSessionFixture(courseId: string): LmsSessionRequest {
  return {
    learner_id: "demo-learner",
    course_id: courseId,
    resource_id: "demo-resource",
    lesson_id: "demo-lesson",
    // resource IDs, not types. SessionBootstrap replaces these placeholders
    // with the course's real resource IDs before launching (see
    // SessionBootstrap bootstrap); they are only a fallback for when the
    // resources endpoint is unreachable.
    resource_scope: ["res_video", "res_pdf", "res_ppt"],
    current_time_ms: 0,
    // signature intentionally OMITTED in dev: the backend only bypasses auth
    // when signature is ABSENT and the dev secret is active (see
    // apps/api/app/chat/launch.py verify_lms_launch). A present-but-wrong
    // value such as "dev" yields 401. Production LMS launches MUST attach a
    // real HMAC via sign_launch over the canonical payload.
    issued_at: Date.now(),
  };
}

export const mockSession: LmsSessionResponse = {
  session_id: "mock-session-local",
  session_token: "mock-token",
  learner_id: "demo-learner",
  course_id: "demo-course",
};

export const MOCK_ANSWERS: Record<ChatMode, ChatResponse> = {
  qa: {
    answer:
      "In the portion you have watched so far, the key idea is introduced around the cited timestamp. Pause position is enforced: nothing after your current time was used.",
    citations: [
      {
        source_type: "video",
        video_id: "demo-video",
        start_ms: 12000,
        end_ms: 45000,
        page_no: null,
        slide_no: null,
        chunk_id: "mock-chunk-001",
      },
      {
        source_type: "pdf",
        video_id: "demo-video",
        start_ms: 0,
        end_ms: 0,
        page_no: 3,
        slide_no: null,
        chunk_id: "mock-chunk-002",
      },
    ],
    refused: false,
  },
  explain_simple: {
    answer:
      "Think of it like pausing a recipe video: you only need the steps you have already seen. The concept so far boils down to one simple rule — ask at the moment you get stuck.",
    citations: [
      {
        source_type: "video",
        video_id: "demo-video",
        start_ms: 5000,
        end_ms: 20000,
        page_no: null,
        slide_no: null,
        chunk_id: "mock-chunk-003",
      },
    ],
    refused: false,
  },
  analogy: {
    answer:
      "It is like a bookmark that only lets you peek backwards: everything before your pause is fair game, everything after stays sealed until you watch it.",
    citations: [
      {
        source_type: "pptx",
        video_id: "demo-video",
        start_ms: 0,
        end_ms: 0,
        page_no: null,
        slide_no: 4,
        chunk_id: "mock-chunk-004",
      },
    ],
    refused: false,
  },
  summarize: {
    answer:
      "So far you have covered: (1) the setup shown at the start, (2) the core idea in the middle segment, and (3) one worked example. Flashcards: setup → idea → example.",
    citations: [
      {
        source_type: "video",
        video_id: "demo-video",
        start_ms: 0,
        end_ms: 60000,
        page_no: null,
        slide_no: null,
        chunk_id: "mock-chunk-005",
      },
    ],
    refused: false,
  },
  compare: {
    answer:
      "Concept A (earlier segment) focuses on definitions, while concept B (later but still before your pause) focuses on application. Same foundation, different job.",
    citations: [
      {
        source_type: "video",
        video_id: "demo-video",
        start_ms: 10000,
        end_ms: 30000,
        page_no: null,
        slide_no: null,
        chunk_id: "mock-chunk-006",
      },
      {
        source_type: "video",
        video_id: "demo-video",
        start_ms: 30000,
        end_ms: 55000,
        page_no: null,
        slide_no: null,
        chunk_id: "mock-chunk-007",
      },
    ],
    refused: false,
  },
  quiz: {
    answer:
      "Quick check (covered content only):\n1) What was the key term introduced first?\n2) Why does the worked example matter?\nReply with your answers and I will grade them.",
    citations: [
      {
        source_type: "video",
        video_id: "demo-video",
        start_ms: 15000,
        end_ms: 55000,
        page_no: null,
        slide_no: null,
        chunk_id: "mock-chunk-008",
      },
    ],
    refused: false,
  },
};

export function mockChatAnswer(
  mode: string | undefined,
  message: string,
): ChatResponse {
  if (/spoil|future|later|next chapter/i.test(message)) {
    return {
      answer:
        "I cannot answer that yet — it refers to content after your current position. Watch a little further, then ask again.",
      citations: [],
      refused: true,
    };
  }
  return (
    (MOCK_ANSWERS as Record<string, ChatResponse>)[mode ?? "qa"] ??
    MOCK_ANSWERS.qa
  );
}

export const mockHistory: ChatMessage[] = [];
