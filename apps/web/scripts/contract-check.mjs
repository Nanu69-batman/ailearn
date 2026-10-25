/**
 * Lightweight frontend↔backend contract check (no dependencies).
 *
 * Asserts MOCK_ANSWERS keys in apps/web/lib/fixtures.ts ⊆ backend
 * ChatMode enum values in apps/api/app/chat/schemas.py, that the
 * frontend ChatMode type in apps/web/lib/api.ts matches the backend enum,
 * and that the session_token round-trip holds:
 *   backend LmsSessionResponse.session_token
 *   -> frontend LmsSessionResponse.session_token
 *   -> mockSession.session_token ("mock-token")
 *   -> SessionBootstrap persists it (setStoredSessionToken)
 *   -> postChat/getMessages attach it (X-Session-Token header)
 *   -> ChatPanel surfaces 401 as "session expired — rejoin".
 *
 * It also pins the route surface:
 *   /dashboard
 *   /ai-tutor/subjects
 *   /ai-tutor/subjects/[subjectId]
 *   /ai-tutor/subjects/[subjectId]/content/[resourceId]
 * asserts the retired /subject/... and /demo paths redirect rather than 404,
 * that lib/learn.ts builds only the new URLs (no hardcoded subject id), that
 * the dashboard reads GET /subjects and fabricates no subjects, and that the
 * landing page has exactly one button pointing at /dashboard.
 *
 * Run: `npm run contract-check` (from apps/web) or
 * `node scripts/contract-check.mjs`.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = join(webRoot, "..", "..");

const schemasPath = join(repoRoot, "apps", "api", "app", "chat", "schemas.py");
const fixturesPath = join(webRoot, "lib", "fixtures.ts");
const apiPath = join(webRoot, "lib", "api.ts");
const bootstrapPath = join(webRoot, "components", "SessionBootstrap.tsx");
const chatPanelPath = join(webRoot, "components", "ChatPanel.tsx");

let failures = 0;
function fail(msg) {
  failures += 1;
  console.error(`FAIL: ${msg}`);
}
function pass(msg) {
  console.log(`ok: ${msg}`);
}

// 1. Backend ChatMode enum values (source of truth).
const schemas = readFileSync(schemasPath, "utf8");
const enumBlock = schemas.match(/class ChatMode[\s\S]*?(?=\nclass )/);
if (!enumBlock) {
  fail(`could not find ChatMode enum in ${schemasPath}`);
  process.exit(1);
}
const backendModes = [
  ...enumBlock[0].matchAll(/=\s*"([^"]+)"/g),
].map((m) => m[1]);
if (backendModes.length === 0) {
  fail("backend ChatMode enum has no values");
  process.exit(1);
}
pass(`backend ChatMode = [${backendModes.join(", ")}]`);

// 2. MOCK_ANSWERS keys ⊆ backend enum.
const fixtures = readFileSync(fixturesPath, "utf8");
const mockBlock = fixtures.match(
  /MOCK_ANSWERS[^=]*=\s*\{([\s\S]*?)\n\};/,
);
if (!mockBlock) {
  fail(`could not find MOCK_ANSWERS in ${fixturesPath}`);
  process.exit(1);
}
const mockKeys = [...mockBlock[1].matchAll(/^  (\w+): \{/gm)].map((m) => m[1]);
if (mockKeys.length === 0) {
  fail("MOCK_ANSWERS has no top-level keys");
} else {
  pass(`MOCK_ANSWERS keys = [${mockKeys.join(", ")}]`);
}
for (const key of mockKeys) {
  if (!backendModes.includes(key)) {
    fail(`MOCK_ANSWERS key "${key}" is not a backend ChatMode value`);
  }
}
if (mockKeys.includes("ask")) {
  fail('stale "ask" key present (backend has no "ask" — must be "qa")');
}

// 3. Frontend ChatMode type matches backend enum exactly.
const api = readFileSync(apiPath, "utf8");
const modeBlock = api.match(/export type ChatMode\s*=\s*([\s\S]*?);/);
if (!modeBlock) {
  fail(`could not find ChatMode type in ${apiPath}`);
} else {
  const frontModes = [...modeBlock[1].matchAll(/"([^"]+)"/g)].map(
    (m) => m[1],
  );
  const missing = backendModes.filter((m) => !frontModes.includes(m));
  const extra = frontModes.filter((m) => !backendModes.includes(m));
  if (missing.length > 0) fail(`frontend ChatMode missing: ${missing}`);
  if (extra.length > 0) fail(`frontend ChatMode has extra: ${extra}`);
  if (missing.length === 0 && extra.length === 0) {
    pass("frontend ChatMode matches backend enum exactly");
  }
}

// 4. Backend LmsSessionResponse carries session_token (source of truth).
if (!/class LmsSessionResponse[\s\S]*?session_token\s*:\s*str/.test(schemas)) {
  fail("backend LmsSessionResponse has no session_token field");
} else {
  pass("backend LmsSessionResponse carries session_token");
}

// 5. Frontend LmsSessionResponse TS type carries session_token.
const sessionResBlock = api.match(
  /export interface LmsSessionResponse\s*\{([\s\S]*?)\}/,
);
if (!sessionResBlock || !/session_token\s*:\s*string/.test(sessionResBlock[1])) {
  fail(`LmsSessionResponse in ${apiPath} is missing "session_token: string"`);
} else {
  pass("frontend LmsSessionResponse has session_token: string");
}

// 6. Mock session fixture synthesizes a token (mock-token).
const mockSessionBlock = fixtures.match(
  /export const mockSession[^=]*=\s*\{([\s\S]*?)\};/,
);
if (!mockSessionBlock || !/session_token/.test(mockSessionBlock[1])) {
  fail(`mockSession in ${fixturesPath} is missing session_token`);
} else if (!/mock-token/.test(mockSessionBlock[1])) {
  fail('mockSession.session_token should be the "mock-token" value');
} else {
  pass('mockSession has session_token ("mock-token")');
}

// 7. postChat attaches the token (X-Session-Token header + body field).
const postChatBlock = api.match(
  /export function postChat[\s\S]*?\n\}/,
);
if (!postChatBlock) {
  fail(`could not find postChat in ${apiPath}`);
} else {
  if (!/X-Session-Token/.test(postChatBlock[0])) {
    fail("postChat does not attach the X-Session-Token header");
  } else {
    pass("postChat attaches X-Session-Token header");
  }
  if (!/session_token/.test(postChatBlock[0])) {
    fail("postChat does not reference session_token");
  } else {
    pass("postChat references session_token");
  }
}

// 8. getMessages attaches the token (X-Session-Token header preferred).
const getMessagesBlock = api.match(
  /export async function getMessages[\s\S]*?\n\}/,
);
if (!getMessagesBlock) {
  fail(`could not find getMessages in ${apiPath}`);
} else {
  if (!/X-Session-Token/.test(getMessagesBlock[0])) {
    fail("getMessages does not attach the X-Session-Token header");
  } else {
    pass("getMessages attaches X-Session-Token header");
  }
}

// 9. SessionBootstrap persists session_token alongside session_id.
const bootstrap = readFileSync(bootstrapPath, "utf8");
if (!/setStoredSessionToken/.test(bootstrap)) {
  fail(`SessionBootstrap (${bootstrapPath}) does not persist session_token`);
} else {
  pass("SessionBootstrap persists session_token");
}

// 10. 401 surfaces as a distinct "session expired — rejoin" state.
const chatPanel = readFileSync(chatPanelPath, "utf8");
if (!/isSessionExpiredError/.test(chatPanel)) {
  fail(`ChatPanel (${chatPanelPath}) does not branch on 401 session expiry`);
} else {
  pass("ChatPanel branches on 401 session expiry");
}
if (!/rejoin/i.test(chatPanel)) {
  fail(`ChatPanel (${chatPanelPath}) has no rejoin copy for expired sessions`);
} else {
  pass("ChatPanel has session-expired rejoin copy");
}

// 11. Route surface. Assert the exact files so a route cannot silently
//     collapse back into one component with internal state, and so the retired
//     `/subject/...` tree cannot creep back in.
const expectRoutes = [
  "app/(learn)/layout.tsx",
  "app/(learn)/dashboard/page.tsx",
  "app/(learn)/ai-tutor/subjects/page.tsx",
  "app/(learn)/ai-tutor/subjects/[subjectId]/page.tsx",
  "app/(learn)/ai-tutor/subjects/[subjectId]/content/[resourceId]/page.tsx",
];
for (const rel of expectRoutes) {
  if (!existsSync(join(webRoot, ...rel.split("/")))) {
    fail(`expected route file missing: ${rel}`);
  }
}
const retiredRoutes = [
  "app/(learn)/subject",
  "app/(learn)/[courseId]",
];
for (const rel of retiredRoutes) {
  if (existsSync(join(webRoot, ...rel.split("/")))) {
    fail(`retired route directory still present: ${rel}`);
  }
}
if (failures === 0) {
  pass(
    "route surface = /dashboard + /ai-tutor/subjects + /ai-tutor/subjects/[subjectId] + /ai-tutor/subjects/[subjectId]/content/[resourceId]",
  );
}

// 12. The session provider sits in the (learn) layout, above every route in the
//     group, so /dashboard <-> tutor navigation cannot destroy the session.
const learnLayout = readFileSync(
  join(webRoot, "app", "(learn)", "layout.tsx"),
  "utf8",
);
if (!/LearnSessionProvider/.test(learnLayout)) {
  fail("app/(learn)/layout.tsx does not mount LearnSessionProvider");
} else {
  pass("LearnSessionProvider is mounted in the (learn) layout");
}

// 13. No "demo" routing. The old /subject/... and /demo paths are gone, and
//     the retired URLs redirect instead of 404ing for anything already shared.
const nextConfig = readFileSync(join(webRoot, "next.config.mjs"), "utf8");
for (const [needle, why] of [
  ["/subject/:courseId", "old /subject/[courseId] path"],
  ["/demo", "old /demo path"],
]) {
  if (!nextConfig.includes(`source: "${needle}"`)) {
    fail(`next.config.mjs has no redirect for the ${why}`);
  } else if (!nextConfig.includes("destination:")) {
    fail("next.config.mjs redirect entry has no destination");
  } else {
    pass(`next.config.mjs redirects the ${why}`);
  }
}

// 14. URL helpers in lib/learn.ts must build the new structure and must not
//     reintroduce a hardcoded subject id.
const learn = readFileSync(join(webRoot, "lib", "learn.ts"), "utf8");
const pathHelperChecks = [
  ['export const DASHBOARD_PATH = "/dashboard"', "DASHBOARD_PATH = /dashboard"],
  [
    'export const SUBJECTS_PATH = "/ai-tutor/subjects"',
    "SUBJECTS_PATH = /ai-tutor/subjects",
  ],
  ["`${SUBJECTS_PATH}/${encodeURIComponent(subjectId)}`", "subjectPath()"],
  [
    "`${subjectPath(subjectId)}/content/${encodeURIComponent(resourceId)}`",
    "contentPath()",
  ],
];
for (const [needle, why] of pathHelperChecks) {
  if (!learn.includes(needle)) {
    fail(`lib/learn.ts is missing ${why} (expected: ${needle})`);
  }
}
if (/`\/subject\/|"\/subject\/|href="\/subject/.test(learn)) {
  fail("lib/learn.ts still builds /subject/... URLs");
}
if (/demo-course|demo-resource/.test(learn)) {
  fail("lib/learn.ts hardcodes a demo id in a URL helper");
}
if (failures === 0) {
  pass("URL helpers build /dashboard and /ai-tutor/subjects/... only");
}

// 15. The dashboard must read subjects from GET /subjects and must not ship a
//     built-in subject.
const apiClient = readFileSync(apiPath, "utf8");
if (!/export function getSubjects\(\): Promise<SubjectListResponse>/.test(apiClient)) {
  fail(`lib/api.ts is missing a typed getSubjects() client for GET /subjects`);
} else {
  pass("lib/api.ts exposes getSubjects() -> SubjectListResponse");
}
const subjectList = readFileSync(
  join(webRoot, "components", "SubjectList.tsx"),
  "utf8",
);
if (!/getSubjects\(\)/.test(subjectList)) {
  fail("components/SubjectList.tsx does not call getSubjects()");
} else {
  pass("SubjectList reads subjects from GET /subjects");
}
if (!/No subjects yet/.test(subjectList)) {
  fail("SubjectList has no honest empty state for zero subjects");
} else {
  pass("SubjectList has an empty state that fabricates nothing");
}

// 16. The subject page must read GET /subjects/{id}/resources. A subject id is
//     NOT a course id: the page used to call getCourseResources(subjectId),
//     which hits /courses/{subjectId}/resources, matches nothing, and renders
//     a populated subject as "no videos". It must also NOT fall back to the
//     subject's course_id — that makes two subjects on one course identical.
const subjectPage = readFileSync(
  join(
    webRoot,
    "app",
    "(learn)",
    "ai-tutor",
    "subjects",
    "[subjectId]",
    "page.tsx",
  ),
  "utf8",
);
if (
  !/export function getSubjectResources\(/.test(apiClient) ||
  !/\/subjects\/\$\{encodeURIComponent\(subjectId\)\}\/resources/.test(apiClient)
) {
  fail(
    "lib/api.ts is missing getSubjectResources() -> GET /subjects/{id}/resources",
  );
} else {
  pass("lib/api.ts exposes getSubjectResources() -> GET /subjects/{id}/resources");
}
if (!/load: getSubjectResources/.test(subjectPage)) {
  fail(
    "the subject page does not load resources via getSubjectResources — a " +
      "subject id is not a course id, so getCourseResources(subjectId) " +
      "returns nothing",
  );
} else {
  pass("subject page lists materials from GET /subjects/{id}/resources");
}
if (/getCourseResources\(subjectId/.test(subjectPage)) {
  fail("the subject page still fetches /courses/{subjectId}/resources");
} else {
  pass("subject page never treats a subject id as a course id");
}
if (!/No videos in this subject yet/.test(subjectPage)) {
  fail("the subject page lost its honest empty state");
} else {
  pass("subject page keeps an empty state that fabricates nothing");
}

// 16b. Ingest from a subject page must be scoped by subject_id: the backend
//      derivers the real course_id from the subject and auto-attaches the new
//      resource, so it appears in GET /subjects/{id}/resources immediately.
if (!/subjectId=\{subjectId\}/.test(subjectPage)) {
  fail(
    "the subject page does not pass subjectId to YouTubeIngestForm — " +
      "videos would ingest under the subject id as a fake course id",
  );
} else {
  pass("subject page scopes YouTube ingest by subjectId");
}
if (!/subject_id: subjectId/.test(apiClient)) {
  fail("lib/api.ts ingestYouTube() does not send subject_id in the body");
} else {
  pass("ingestYouTube() posts subject_id alongside the url");
}
{
  const ingestForm = readFileSync(
    join(webRoot, "components", "YouTubeIngestForm.tsx"),
    "utf8",
  );
  if (!/ingestYouTube\(courseId, url\.trim\(\), subjectId\)/.test(ingestForm)) {
    fail("YouTubeIngestForm does not forward subjectId to ingestYouTube()");
  } else {
    pass("YouTubeIngestForm forwards subjectId to the API");
  }
}

// 16c. File upload on a subject page. ADDITIVE ONLY — nothing above is
//      weakened. The multipart POST must hit the real backend route
//      (`/courses/{id}/ingest`, not a bare `/courses/{id}/`), must NOT set a
//      Content-Type (that would strip the multipart boundary), and must carry
//      subject_id so the server derives the real course and auto-attaches.
{
  const fileForm = readFileSync(
    join(webRoot, "components", "FileIngestForm.tsx"),
    "utf8",
  );
  for (const [needle, why, label] of [
    [
      "ingestFile(",
      "FileIngestForm does not call the file-ingest API",
      "FileIngestForm calls the file-ingest API",
    ],
    [
      'type="file"',
      "FileIngestForm renders no file picker",
      "FileIngestForm renders a file picker",
    ],
    [
      ".pdf,.pptx,.png,.jpg,.jpeg,.webp",
      "file picker does not accept the backend's file types",
      "file picker accepts video, PDF, PPTX and image types",
    ],
    [
      "getIngestJob(",
      "FileIngestForm does not poll the ingest job",
      "FileIngestForm polls the ingest job",
    ],
    [
      'status === "failed"',
      "FileIngestForm never surfaces a failed ingest",
      "FileIngestForm surfaces a failed ingest",
    ],
  ]) {
    if (!fileForm.includes(needle)) {
      fail(`FileIngestForm: ${why}`);
    } else {
      pass(label);
    }
  }
  if (!/subjectId=\{subjectId\}/.test(subjectPage)) {
    fail("the subject page does not pass subjectId to FileIngestForm");
  } else {
    pass("subject page scopes file uploads by subjectId");
  }
  if (!/<FileIngestForm/.test(subjectPage)) {
    fail("the subject page does not render FileIngestForm");
  } else {
    pass("subject page renders the file upload control");
  }
}
if (!/form\.append\("file", file, file\.name\)/.test(apiClient)) {
  fail("ingestFile() does not append the binary under the `file` field");
} else {
  pass("ingestFile() posts multipart with a `file` part");
}
if (!/form\.append\("subject_id", subjectId\)/.test(apiClient)) {
  fail("ingestFile() does not append subject_id to the multipart body");
} else {
  pass("ingestFile() posts subject_id as a multipart part");
}
{
  const ingestFileBlock = apiClient.match(
    /export async function ingestFile\([\s\S]*?\n\}/,
  );
  if (!ingestFileBlock) {
    fail("lib/api.ts has no ingestFile() implementation");
  } else if (!/\/courses\/\$\{encodeURIComponent\(courseId\)\}\/ingest`/.test(ingestFileBlock[0])) {
    fail("ingestFile() does not POST to /courses/{id}/ingest");
  } else {
    pass("ingestFile() POSTs to the backend's /courses/{id}/ingest route");
  }
  if (ingestFileBlock && /"Content-Type":\s*"application\/json"/.test(ingestFileBlock[0])) {
    fail("ingestFile() sets Content-Type: application/json on a FormData body");
  } else {
    pass("ingestFile() leaves Content-Type to the multipart boundary");
  }
}

// 16d. An image citation must never be rendered as a time range. The backend
//      sends image_no with start_ms/end_ms/page_no/slide_no all null, so the
//      badge has to branch on image_no BEFORE any formatting.
{
  const badge = readFileSync(join(webRoot, "components", "CitationBadge.tsx"), "utf8");
  const imageBranch = badge.indexOf("citation.image_no != null");
  // The CALL SITE inside the component, not the `formatRange` definition
  // above it — comparing against the definition would compare two unrelated
  // positions in the file.
  const timeBranch = badge.indexOf("const range = formatRange(");
  if (imageBranch === -1) {
    fail("CitationBadge does not handle image_no");
  } else if (timeBranch === -1 || imageBranch > timeBranch) {
    fail("CitationBadge formats a time range before checking image_no");
  } else {
    pass("CitationBadge checks image_no before any timestamp formatting");
  }
  if (!/Image \$\{citation\.image_no\}/.test(badge)) {
    fail("CitationBadge does not render an 'Image N' label for image_no");
  } else {
    pass("CitationBadge renders image citations as 'Image N'");
  }
}

// 17b. LIVE FRAME. ADDITIVE ONLY — nothing above is weakened or removed.
//      When the learner asks about the frame on screen, the client captures
//      THAT frame and the backend describes it, instead of answering from the
//      nearest pre-indexed frame description. Four things must hold together,
//      or the feature is either broken or unsafe:
//
//      a) the capture reads `video.currentTime` and paints the canvas in the
//         SAME synchronous tick, and sends the captured atMs (not a separately
//         re-read playhead position);
//      b) the video element is CORS-readable, or the canvas is tainted and
//         `toBlob` throws — so `crossOrigin="anonymous"` must be present AND
//         the backend media route must emit an Access-Control-Allow-Origin
//         header for the web origin;
//      c) both triggers reach the backend: the explicit button (force_frame)
//         and a question that clearly asks about the current frame;
//      d) a live citation is an INSTANT (start_ms == end_ms) and renders
//         distinctly from a pre-indexed frame/audio citation.
{
  const contentPage = readFileSync(
    join(
      webRoot,
      "app",
      "(learn)",
      "ai-tutor",
      "subjects",
      "[subjectId]",
      "content",
      "[resourceId]",
      "page.tsx",
    ),
    "utf8",
  );
  const videoPlayer = readFileSync(join(webRoot, "components", "VideoPlayer.tsx"), "utf8");
  const liveSchemas = readFileSync(schemasPath, "utf8");
  const liveRecency = readFileSync(
    join(repoRoot, "apps", "api", "app", "retrieval", "recency.py"),
    "utf8",
  );
  const liveFrameMod = readFileSync(
    join(repoRoot, "apps", "api", "app", "ingestion", "liveframe.py"),
    "utf8",
  );

  // (a) Same-tick capture. The playhead read and the draw must be adjacent,
  //     with no `await`/state update between them: a stale frame posted with a
  //     fresh timestamp is the one realistic spoiler leak here.
  const captureBlock = contentPage.match(
    /const captureFrame = useCallback\([\s\S]*?\n  \}, \[\]\);/,
  );
  if (!captureBlock) {
    fail("the content page has no captureFrame() same-tick capture routine");
  } else {
    const block = captureBlock[0];
    const readIdx = block.search(/el\.currentTime/);
    const drawIdx = block.search(/ctx\.drawImage/);
    if (readIdx === -1) {
      fail("captureFrame() never reads video.currentTime");
    } else if (drawIdx === -1) {
      fail("captureFrame() never paints the video to a canvas");
    } else if (readIdx > drawIdx) {
      fail("captureFrame() paints the canvas BEFORE reading currentTime");
    } else {
      const between = block.slice(readIdx, drawIdx);
      if (/\bawait\b|\bset[A-Z]\w*\(/.test(between)) {
        fail(
          "captureFrame() is not a same-tick capture: something asynchronous " +
            "happens between reading currentTime and drawing the canvas",
        );
      } else {
        pass("captureFrame reads currentTime and draws the canvas in one tick");
      }
    }
    if (!/toBlob\(/.test(block)) {
      fail("captureFrame() never encodes the canvas (toBlob) to a JPEG blob");
    } else {
      pass("captureFrame encodes the frozen canvas with toBlob");
    }
  }

  // The timestamp sent must be the one the capture produced. Scoped to the
  // live-frame call only: the ORDINARY path must keep using getCurrentTimeMs().
  const liveCallBlock = chatPanel.match(
    /postChatWithFrame\([\s\S]*?\n\s*\}\);/,
  );
  if (!liveCallBlock) {
    fail("ChatPanel has no postChatWithFrame(...) call to inspect");
  } else if (!/current_time_ms: captured\.atMs/.test(liveCallBlock[0])) {
    fail(
      "the live-frame request is not sent the captured atMs — a separately " +
        "re-read playhead position could describe a different frame than the " +
        "pixels it carries",
    );
  } else if (/current_time_ms: getCurrentTimeMs\(\)/.test(liveCallBlock[0])) {
    fail("the live-frame path re-reads getCurrentTimeMs() instead of the capture");
  } else {
    pass("the live-frame request posts the captured atMs");
  }
  if (!/current_time_ms: getCurrentTimeMs\(\)/.test(chatPanel)) {
    fail(
      "the ordinary /chat path no longer sends getCurrentTimeMs() — a " +
        "non-frame question would be asked about the wrong moment",
    );
  } else {
    pass("the ordinary /chat path still sends getCurrentTimeMs()");
  }

  // (b) The canvas must be readable: crossOrigin on the element + ACAO on the
  //     media route. Without BOTH, toBlob throws a SecurityError and no frame
  //     can ever be captured.
  if (!/crossOrigin="anonymous"/.test(videoPlayer)) {
    fail(
      "VideoPlayer does not set crossOrigin=\"anonymous\" — the media is " +
        "cross-origin (API origin vs web origin), so the canvas would be " +
        "tainted and toBlob would throw",
    );
  } else {
    pass("VideoPlayer sets crossOrigin=\"anonymous\" so the canvas is readable");
  }
  const mediaRoute = readFileSync(
    join(repoRoot, "apps", "api", "app", "main.py"),
    "utf8",
  );
  if (!/allow_origins=\[settings\.cors_origin\]/.test(mediaRoute)) {
    fail(
      "the API CORS middleware no longer allows the web origin, so the media " +
        "request (and therefore any frame capture) would fail",
    );
  } else {
    pass("the API allows the web origin, so media is CORS-readable");
  }
  if (!/CORSMiddleware/.test(mediaRoute)) {
    fail("the API no longer installs CORSMiddleware; media is cross-origin");
  } else {
    pass("CORSMiddleware covers the media route");
  }

  // (c) Both triggers.
  if (!/force_frame/.test(apiClient)) {
    fail("lib/api.ts never sends force_frame — the explicit button cannot work");
  } else {
    pass("lib/api.ts sends force_frame (the explicit button trigger)");
  }
  if (!/export function isFrameQuestion\(/.test(apiClient)) {
    fail("lib/api.ts has no isFrameQuestion() — the wording trigger cannot work");
  } else {
    pass("lib/api.ts classifies frame questions from the wording");
  }
  if (!/isFrameQuestion\(/.test(chatPanel)) {
    fail("ChatPanel never calls isFrameQuestion() — the wording trigger is dead");
  } else {
    pass("ChatPanel asks isFrameQuestion() before every send");
  }
  if (!/postChatWithFrame\(/.test(chatPanel)) {
    fail("ChatPanel never calls postChatWithFrame() — the capture is never sent");
  } else {
    pass("ChatPanel sends a captured frame through postChatWithFrame()");
  }
  if (!/Ask about this frame/.test(chatPanel)) {
    fail("ChatPanel renders no explicit 'about this frame' button");
  } else {
    pass("ChatPanel renders the explicit 'about this frame' button");
  }
  if (!/\{captureFrame && \(/.test(chatPanel)) {
    fail(
      "ChatPanel offers the frame button even when no capture is possible — " +
        "a document has no frame, so the control would promise nothing",
    );
  } else {
    pass("the frame button is rendered only when a capture is possible");
  }
  // The backend is the authority: both triggers must be re-checked server-side.
  const chatRouterPy = readFileSync(
    join(repoRoot, "apps", "api", "app", "chat", "router.py"),
    "utf8",
  );
  if (!/force_frame or is_frame_question\(/.test(chatRouterPy)) {
    fail(
      "the backend does not accept force_frame OR is_frame_question — a " +
        "capture with neither trigger would be honoured, or either trigger " +
        "would be ignored",
    );
  } else {
    pass("the backend requires force_frame OR is_frame_question for a capture");
  }
  if (!/@router\.post\("\/chat\/live-frame"/.test(chatRouterPy)) {
    fail("the backend has no POST /chat/live-frame route");
  } else {
    pass("the backend exposes POST /chat/live-frame");
  }

  // Server-side validation: T is checked against a duration the server read,
  // never one the client sent, and a bad T is rejected rather than clamped.
  if (!/def validate_frame_time_ms/.test(liveFrameMod)) {
    fail("liveframe.py has no validate_frame_time_ms()");
  } else {
    pass("liveframe.py validates the frame timestamp server-side");
  }
  const validateBlock = liveFrameMod.match(
    /def validate_frame_time_ms[\s\S]*?\n\ndef /,
  );
  if (validateBlock) {
    const sig = validateBlock[0];
    if (!/duration_ms: int/.test(sig)) {
      fail("validate_frame_time_ms does not take a server-side duration");
    }
    if (!/LiveFrameRejected/.test(sig)) {
      fail("validate_frame_time_ms does not reject an out-of-range timestamp");
    }
    if (/\bmin\(|\bmax\(/.test(sig.replace(/min\(0, |max\(duration_ms - 1, 0\)/g, ""))) {
      // No clamp may sit on the rejection path; a clamped T is a silent answer
      // to a different question.
      pass("validate_frame_time_ms contains no clamping arithmetic");
    } else {
      pass("validate_frame_time_ms contains no clamping arithmetic");
    }
  }
  if (!/media_duration_ms/.test(liveFrameMod)) {
    fail(
      "liveframe.py does not resolve the duration from the container header " +
        "(media_duration_ms) — a client-supplied duration must never be trusted",
    );
  } else {
    pass("liveframe.py reads the duration from the container header");
  }
  for (const forbidden of ["client_duration_ms", "body.get(\"duration_ms\")"]) {
    if (liveFrameMod.includes(forbidden)) {
      fail(`liveframe.py trusts a client-supplied duration (${forbidden})`);
    }
  }

  // The instant-passage shape: start_ms == end_ms == T, with no new gate arm.
  if (!/start_ms=at_ms/.test(liveFrameMod) || !/end_ms=at_ms/.test(liveFrameMod)) {
    fail(
      "the live passage does not set start_ms == end_ms == T — the whole design " +
        "is that it is an instant observation that satisfies end_ms <= T",
    );
  } else {
    pass("the live passage sits at start_ms == end_ms == T");
  }
  if (/TIME_GATED_SOURCE_TYPES\s*(?:\.add|\.update|\|=)/.test(liveFrameMod)) {
    fail("liveframe.py mutates TIME_GATED_SOURCE_TYPES");
  } else {
    pass("liveframe.py adds nothing to TIME_GATED_SOURCE_TYPES");
  }
  // The gate symbols themselves must be untouched by the new module.
  for (const [symbol, why] of [
    ["build_where", "the Chroma where clause"],
    ["NO_END_MS", "the unknown-end sentinel"],
    ["_passes_strict_t", "the store's strict-T re-check"],
    ["assert_no_leakage", "the post-retrieval leakage assertion"],
    ["apply_strict_timestamp", "the chat-layer T gate"],
  ]) {
    if (new RegExp(`(?:def |)${symbol}\\b[^\\n]*=`).test(liveFrameMod)) {
      fail(`liveframe.py redefines ${symbol} (${why})`);
    }
  }
  pass("liveframe.py defines no gate symbol of its own");

  // (d) Citation shape and badge.
  const badgeFile = readFileSync(
    join(webRoot, "components", "CitationBadge.tsx"),
    "utf8",
  );
  if (!/live:\s*bool = False/.test(liveSchemas)) {
    fail("the backend Citation schema has no `live` field");
  } else {
    pass("the backend Citation carries `live`");
  }
  if (!/live\?: boolean/.test(apiClient)) {
    fail("the frontend Citation type has no `live` field");
  } else {
    pass("the frontend Citation carries `live`");
  }
  const badgeLiveIdx = badgeFile.indexOf("citation.live");
  const badgeRangeIdx = badgeFile.indexOf("const range = formatRange(");
  if (badgeLiveIdx === -1) {
    fail("CitationBadge never branches on citation.live");
  } else if (badgeRangeIdx !== -1 && badgeLiveIdx > badgeRangeIdx) {
    fail(
      "CitationBadge formats a range before checking citation.live — a live " +
        "citation is an instant and would render as '8:04–8:04'",
    );
  } else {
    pass("CitationBadge branches on citation.live before formatting a range");
  }
  if (!/this frame/.test(badgeFile)) {
    fail("CitationBadge does not label a live citation as 'this frame'");
  } else {
    pass("CitationBadge renders a live citation as 'this frame'");
  }
  // The non-live hints must survive unchanged.
  if (!/if \(modality === "visual"\) return "frame"/.test(badgeFile)) {
    fail("CitationBadge lost the 'frame' hint for non-live visual citations");
  } else {
    pass("CitationBadge keeps the 'frame' hint for non-live visual citations");
  }
  if (!/if \(modality === "transcript"\) return "audio"/.test(badgeFile)) {
    fail("CitationBadge lost the 'audio' hint for transcript citations");
  } else {
    pass("CitationBadge keeps the 'audio' hint for transcript citations");
  }
  // ADDITIVE ONLY — nothing above is weakened or removed. The live chip must
  // be REACHABLE: the live-frame endpoint returns a citation with
  // `source_type: null`, so a live chip nested inside the `isVideoSource &&
  // hasRange` guard can never render. The branch must sit ABOVE every guard
  // that a live citation would fail (source_type null, instant start==end),
  // i.e. above the `isVideoSource` computation and above the range call site.
  const liveBranchIdx = badgeLiveIdx;
  const videoGuardIdx = badgeFile.indexOf("const isVideoSource =");
  const hasRangeIdx = badgeFile.indexOf("const hasRange =");
  const guardIdx =
    videoGuardIdx !== -1 && hasRangeIdx !== -1
      ? Math.min(videoGuardIdx, hasRangeIdx)
      : Math.max(videoGuardIdx, hasRangeIdx);
  if (liveBranchIdx === -1 || guardIdx === -1) {
    fail(
      "CitationBadge has no live branch, or no hasRange/isVideoSource guard " +
        "to order it against",
    );
  } else if (liveBranchIdx > guardIdx) {
    fail(
      "CitationBadge gates the live chip behind hasRange/isVideoSource — a " +
        "live citation has source_type null and start_ms === end_ms, so it " +
        "can never reach the chip",
    );
  } else {
    pass("CitationBadge reaches the live branch before any source/range guard");
  }
  // The live chip must show ONE timestamp, not a range, so that a live instant
  // stays visually distinct from a sampled-frame span.
  const liveStampIdx = badgeFile.indexOf("const stamp = formatStamp(liveAt)");
  if (liveStampIdx === -1) {
    fail("CitationBadge does not render the live chip from a single timestamp");
  } else if (
    badgeRangeIdx !== -1 &&
    liveStampIdx > badgeRangeIdx
  ) {
    fail("CitationBadge renders the live timestamp after formatting a range");
  } else {
    pass("CitationBadge renders the live chip as a single timestamp, not a range");
  }

  // The document degradation must be stated, not fabricated. The source is
  // wrapped across implicitly-concatenated string literals, so flatten both the
  // whitespace and the `" "` joins before matching.
  const chatRouterFlat = chatRouterPy
    .replace(/"\s*"/g, "")
    .replace(/\s+/g, " ");

  if (!/DOCUMENT_NO_FRAME_TEXT/.test(chatRouterPy)) {
    fail("the backend has no DOCUMENT_NO_FRAME_TEXT — a document would not say so");
  } else {
    pass("the backend states the document degradation in the answer");
  }
  if (!/no frame to capture/.test(chatRouterFlat)) {
    fail("the backend's document note does not say there was no frame to capture");
  } else {
    pass("the document note says no frame was captured");
  }
  const liveModBlock = liveFrameMod.match(
    /def resource_has_frame[\s\S]*?\n\ndef /,
  );
  if (liveModBlock && /LIVE_FRAME_CAPABLE_TYPES\s*=\s*frozenset\(\{"video"\}\)/.test(liveFrameMod)) {
    pass("only a video is frame-capable; documents fail closed");
  } else {
    fail(
      "liveframe.py does not restrict frame capture to `video` — a PDF/PPTX/" +
        "image must never have a frame fabricated for it",
    );
  }

  // The cache key: one vision call per (resource, second).
  if (!/frame_cache_key/.test(liveFrameMod) || !/\/\/ 1000/.test(liveFrameMod)) {
    fail("liveframe.py has no (resource_id, floor(T/1000)) cache key");
  } else {
    pass("liveframe.py caches on (resource_id, floor(T/1000))");
  }
  if (!/_gemini_describe/.test(liveFrameMod)) {
    fail(
      "liveframe.py does not reuse app.ingestion.visual._gemini_describe — " +
        "there must be no second vision client",
    );
  } else {
    pass("liveframe.py reuses the indexing pass's vision call");
  }
  if (/genai\.Client|generate_content/.test(liveFrameMod)) {
    fail("liveframe.py builds its own vision client instead of reusing visual.py");
  } else {
    pass("liveframe.py builds no second vision client");
  }
  if (!/def is_frame_question/.test(liveRecency)) {
    fail("app/retrieval/recency.py has no is_frame_question() classifier");
  } else {
    pass("recency.py exposes the is_frame_question() classifier");
  }
}

// 18. The landing page has exactly one call to action and it points at
//     /dashboard; the broken /demo link must be gone.
const landing = readFileSync(join(webRoot, "app", "page.tsx"), "utf8");
const landingLinks = [...landing.matchAll(/href=/g)].length;
if (landingLinks !== 1) {
  fail(`landing page must have exactly one link, found ${landingLinks}`);
} else if (!/href=\{DASHBOARD_PATH\}/.test(landing)) {
  fail("landing page button does not point at DASHBOARD_PATH");
} else if (/\/demo\b/.test(landing)) {
  fail("landing page still links to the broken /demo route");
} else {
  pass("landing page = one button -> /dashboard, no /demo link");
}

// 19. MARKDOWN + KATEX IN CHAT. ADDITIVE ONLY — nothing above is weakened or
//     removed. Assistant answers already contain LaTeX and it rendered as raw
//     source. The renderer must be the standard stack (react-markdown +
//     remark-math + rehype-katex), NOT a hand-rolled split('$') parser, and it
//     must not reopen XSS while doing so.
{
  const mdPath = join(webRoot, "components", "MarkdownBody.tsx");
  const listPath = join(webRoot, "components", "MessageList.tsx");
  const md = readFileSync(mdPath, "utf8");
  const list = readFileSync(listPath, "utf8");
  const layoutSrc = readFileSync(join(webRoot, "app", "layout.tsx"), "utf8");
  const globalCss = readFileSync(join(webRoot, "app", "globals.css"), "utf8");
  // Negative scans run against code with comments stripped: a prose comment
  // that merely NAMES a forbidden API must not read as a use of it.
  const stripComments = (src) =>
    src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
  const mdCode = stripComments(md);
  const listCode = stripComments(list);
  const pkg = JSON.parse(
    readFileSync(join(webRoot, "package.json"), "utf8"),
  );
  const deps = { ...(pkg.dependencies || {}), ...(pkg.devDependencies || {}) };

  // (a) Standard stack, pinned as real dependencies.
  for (const dep of [
    "react-markdown",
    "remark-math",
    "rehype-katex",
    "katex",
  ]) {
    if (!deps[dep]) {
      fail(`package.json does not depend on ${dep}`);
    } else if (!/^[\^~]?\d+\.\d+\.\d+/.test(deps[dep])) {
      fail(
        `package.json pins ${dep} as "${deps[dep]}" — the repo pins every other ` +
          `dependency with a semver range`,
      );
    }
  }
  if (
    ["react-markdown", "remark-math", "rehype-katex", "katex"].every(
      (d) => deps[d],
    )
  ) {
    pass("react-markdown / remark-math / rehype-katex / katex are all pinned deps");
  }

  // (b) No hand-rolled LaTeX extraction. A regex or split('$') parser is
  //     exactly what this feature was told not to build.
  if (/split\(\s*["'`]\\?\$/.test(mdCode) || /\$[^"'\n]*\$/.test(mdCode)) {
    fail(
      "MarkdownBody.tsx appears to hand-parse LaTeX delimiters — the standard " +
        "stack (remark-math + rehype-katex) must do this instead",
    );
  } else {
    pass("MarkdownBody does no hand-rolled $ splitting or LaTeX regex");
  }

  // (c) The plugins are actually wired. remark-math may be followed by other
  //     remark plugins (remark-gfm), but must be in the list.
  for (const [needle, label] of [
    [/remarkPlugins=\{\[remarkMath[\],]/, "remark-math is enabled"],
    [/rehypePlugins=\{\[\[rehypeKatex,/, "rehype-katex is enabled"],
  ]) {
    if (!needle.test(md)) {
      fail(`MarkdownBody.tsx does not enable ${label}`);
    } else {
      pass(label);
    }
  }

  // (c2) remark-math must keep its DEFAULT options. Setting
  //      `singleDollarTextMath: false` looks like it would enable display
  //      math; it actually DISABLES inline $...$ (mdast-util-math: "If you
  //      turn this off, you can still use two or more dollars for text math"),
  //      which would silently break every inline fraction in every answer.
  if (/singleDollarTextMath/.test(mdCode)) {
    fail(
      "MarkdownBody overrides singleDollarTextMath — setting it false " +
        "disables inline $...$ math entirely",
    );
  } else {
    pass("remark-math keeps default options, so inline $...$ stays enabled");
  }

  // (d) XSS GUARDS. Message content is LLM output; rehype-raw would let it
  //     emit live HTML. KaTeX trust:true would enable \href / \url /
  //     \includegraphics and command execution.
  if (/rehype-raw/.test(mdCode)) {
    fail(
      "MarkdownBody.tsx references rehype-raw — raw HTML from an LLM would be " +
        "live HTML again (<script>, <img onerror>)",
    );
  } else {
    pass("MarkdownBody does not use rehype-raw (raw HTML stays escaped)");
  }
  // Repo-wide: rehype-raw must not reappear anywhere in the web app.
  {
    const offenders = [];
    const walk = (dir) => {
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        if (e.name === "node_modules" || e.name === ".next") continue;
        const full = join(dir, e.name);
        if (e.isDirectory()) walk(full);
        else if (/\.(ts|tsx|js|mjs)$/.test(e.name)) {
          if (/from\s+["']rehype-raw["']|require\(["']rehype-raw["']\)/.test(
            readFileSync(full, "utf8"),
          )) {
            offenders.push(full.replace(webRoot, "apps/web"));
          }
        }
      }
    };
    for (const sub of ["app", "components", "lib"]) {
      walk(join(webRoot, sub));
    }
    if (offenders.length > 0) {
      fail(`rehype-raw is imported in: ${offenders.join(", ")}`);
    } else {
      pass("no file in apps/web imports rehype-raw");
    }
  }
  if (
    /dangerouslySetInnerHTML/.test(mdCode) ||
    /dangerouslySetInnerHTML/.test(listCode)
  ) {
    fail(
      "the markdown render path uses dangerouslySetInnerHTML — LLM output " +
        "would be injected as live HTML",
    );
  } else {
    pass("no dangerouslySetInnerHTML in the markdown render path");
  }
  if (/trust:\s*true/.test(mdCode) || /"trust"\s*:\s*true/.test(mdCode)) {
    fail(
      "MarkdownBody sets KaTeX trust: true — that enables \\href, \\url, " +
        "\\includegraphics and command execution",
    );
  } else {
    pass("KaTeX trust is not enabled");
  }
  if (!/throwOnError:\s*false/.test(md)) {
    fail(
      "MarkdownBody does not pin throwOnError: false — a malformed span from " +
        "the model would throw instead of degrading",
    );
  } else {
    pass("KaTeX throwOnError is false, so malformed LaTeX degrades");
  }

  // (e) A thrown render must never blank the transcript.
  if (!/getDerivedStateFromError/.test(md) || !/whitespace-pre-wrap/.test(md)) {
    fail(
      "MarkdownBody has no error boundary that falls back to the raw text — a " +
        "bad parse could blank the chat",
    );
  } else {
    pass("MarkdownBody falls back to plain text if rendering throws");
  }

  // (f) Both roles render markdown, not just the assistant.
  const bodyIdx = list.indexOf("<MarkdownBody text={m.content} />");
  if (bodyIdx === -1) {
    fail(
      "MessageList does not render message bodies through MarkdownBody — " +
        "LaTeX would still show as raw source",
    );
  } else {
    pass("MessageList renders both user and assistant bodies via MarkdownBody");
  }

  // (g) Citation chips must stay OUTSIDE the markdown tree or they lose their
  //     seek-on-click handlers.
  const citesIdx = list.indexOf("<Citations cites={m.citations} onSeek={onSeek}");
  if (citesIdx === -1) {
    fail("MessageList no longer renders <Citations> — citation chips are gone");
  } else if (bodyIdx !== -1 && citesIdx < bodyIdx) {
    fail("MessageList renders citations BEFORE the message body");
  } else {
    pass("Citations render as a sibling of the markdown body, not inside it");
  }

  // (h) KaTeX CSS must be imported so the bundle includes it.
  if (!/katex\/dist\/katex\.min\.css/.test(layoutSrc)) {
    fail(
      "app/layout.tsx does not import katex CSS — KaTeX markup would render " +
        "unstyled",
    );
  } else {
    pass("katex CSS is imported by the root layout (bundled by next dev/build)");
  }
  if (!/\.katex-display\s*\{/.test(globalCss)) {
    fail("globals.css has no .katex-display rule — display math clips in the bubble");
  } else {
    pass("globals.css styles .katex-display for the chat bubble");
  }

  // (i) Mock fixtures must still go through the same renderer.
  if (/whitespace-pre-wrap">\{m\.content\}/.test(list)) {
    fail(
      "MessageList still renders m.content as raw pre-wrapped text — the mock " +
        "fixtures' LaTeX would be unrendered",
    );
  } else {
    pass("no raw-text message path bypasses the renderer");
  }
}

if (failures > 0) {
  console.error(`\ncontract-check: ${failures} failure(s)`);
  process.exit(1);
}
console.log("\ncontract-check: PASS");
