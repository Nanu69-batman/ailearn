import { NextResponse } from "next/server";

import { getSupabaseServerClient, safeNextPath } from "@/lib/supabase/server";

/** OAuth / email-confirmation landing: trade the code for a cookie session. */
export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
  const next = safeNextPath(url.searchParams.get("next"));

  const supabase = await getSupabaseServerClient();
  if (!supabase) {
    return NextResponse.redirect(new URL(next, url.origin));
  }
  if (code) {
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      return NextResponse.redirect(new URL(next, url.origin));
    }
  }
  const login = new URL("/login", url.origin);
  login.searchParams.set("error", "auth_callback");
  login.searchParams.set("next", next);
  return NextResponse.redirect(login);
}
