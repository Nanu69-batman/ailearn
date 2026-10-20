/**
 * Demo LMS stand-in: signs the launch on the server.
 *
 * A real LMS signs the launch with the shared secret before the learner's
 * browser ever sees it. This route plays that part so the hosted API can run
 * with ALLOW_UNSIGNED_LAUNCH=false: the secret lives only in this server's
 * env (LMS_LAUNCH_SECRET, never NEXT_PUBLIC_*) and never reaches the browser.
 * There is no learner login in the prototype, so it signs any launch it is
 * asked for; what it demonstrates is the signed flow, not identity.
 *
 * The canonical payload mirrors apps/api/app/chat/launch.py
 * `canonical_launch_payload` exactly; change one, change the other.
 *
 * No secret configured (local dev) -> 404, and the client falls back to the
 * unsigned dev launch the API accepts only with the dev secret.
 */
import { createHmac } from "node:crypto";

import type { LmsSessionRequest } from "@/lib/api";

export const runtime = "nodejs";

// Not exported: a route module may only export handlers and route config.
function canonicalLaunchPayload(req: LmsSessionRequest): string {
  return [
    req.learner_id,
    req.course_id,
    req.resource_id,
    req.lesson_id ?? "",
    [...req.resource_scope].sort().join(","),
    String(Math.trunc(req.current_time_ms)),
    String(Math.trunc(req.issued_at)),
  ].join("\n");
}

export async function POST(request: Request): Promise<Response> {
  const secret = process.env.LMS_LAUNCH_SECRET;
  if (!secret) {
    return Response.json({ detail: "launch signing not configured" }, { status: 404 });
  }
  let body: LmsSessionRequest;
  try {
    body = (await request.json()) as LmsSessionRequest;
  } catch {
    return Response.json({ detail: "invalid JSON" }, { status: 422 });
  }
  if (
    typeof body?.learner_id !== "string" ||
    typeof body?.course_id !== "string" ||
    typeof body?.resource_id !== "string" ||
    !Array.isArray(body?.resource_scope) ||
    !body.resource_scope.every((r) => typeof r === "string")
  ) {
    return Response.json({ detail: "invalid launch" }, { status: 422 });
  }
  // The signer, not the browser, decides when the launch was issued.
  const launch: LmsSessionRequest = {
    learner_id: body.learner_id,
    course_id: body.course_id,
    resource_id: body.resource_id,
    lesson_id: body.lesson_id,
    resource_scope: body.resource_scope,
    current_time_ms: Number(body.current_time_ms) || 0,
    issued_at: Date.now(),
  };
  const signature = createHmac("sha256", secret)
    .update(canonicalLaunchPayload(launch))
    .digest("hex");
  return Response.json({ ...launch, signature });
}
