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
import { existsSync, readFileSync } from "node:fs";
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

// 17. The landing page has exactly one call to action and it points at
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

if (failures > 0) {
  console.error(`\ncontract-check: ${failures} failure(s)`);
  process.exit(1);
}
console.log("\ncontract-check: PASS");
