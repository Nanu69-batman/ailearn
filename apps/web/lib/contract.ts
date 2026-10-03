/**
 * TS-level frontend↔backend contract assertions.
 * Checked by `npx tsc --noEmit` (this file is part of the program, no
 * import needed elsewhere). Runtime counterpart: scripts/contract-check.mjs.
 *
 * Backend source of truth: apps/api/app/chat/schemas.py `ChatMode`.
 */
import { CHAT_MODES, type ChatResponse, type LmsSessionResponse } from "./api";
import { MOCK_ANSWERS, mockSession } from "./fixtures";

// MOCK_ANSWERS must cover every backend ChatMode value (missing key = error).
const _exhaustive: Record<(typeof CHAT_MODES)[number], ChatResponse> =
  MOCK_ANSWERS;

// MOCK_ANSWERS must add NO keys beyond the backend enum (extra key = error,
// e.g. the old "ask" that 422d against the live API).
type _ExtraKeys = Exclude<
  keyof typeof MOCK_ANSWERS,
  (typeof CHAT_MODES)[number]
>;
const _noExtras: _ExtraKeys extends never ? true : never = true;

void _exhaustive;
void _noExtras;

// Session token round-trip (backend LmsSessionResponse.session_token):
// the frontend type must carry it and the mock fixture must synthesize it.
type _SessionToken = LmsSessionResponse["session_token"];
const _tokenIsString: _SessionToken extends string ? true : never = true;
const _mockToken: string = mockSession.session_token;

void _tokenIsString;
void _mockToken;

export const CONTRACT_MODES: readonly string[] = CHAT_MODES;
