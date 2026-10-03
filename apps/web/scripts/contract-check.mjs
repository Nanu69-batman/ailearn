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
 * Run: `npm run contract-check` (from apps/web) or
 * `node scripts/contract-check.mjs`.
 */
import { readFileSync } from "node:fs";
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

if (failures > 0) {
  console.error(`\ncontract-check: ${failures} failure(s)`);
  process.exit(1);
}
console.log("\ncontract-check: PASS");
