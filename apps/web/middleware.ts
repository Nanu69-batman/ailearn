import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import {
  SUPABASE_ANON_KEY,
  SUPABASE_URL,
  supabaseConfigured,
} from "@/lib/supabase/config";

/** The bundled demo lesson stays public (docs/SUPABASE_PLAN.md). */
const DEMO_LESSON = /^\/ai-tutor\/subjects\/[^/]+\/content\/res_2e629d682a3d\/?$/;

function isProtected(pathname: string): boolean {
  if (DEMO_LESSON.test(pathname)) return false;
  return (
    pathname === "/dashboard" ||
    pathname.startsWith("/dashboard/") ||
    pathname === "/ai-tutor" ||
    pathname.startsWith("/ai-tutor/") ||
    pathname === "/malai" ||
    pathname.startsWith("/malai/") ||
    pathname === "/classes" ||
    pathname.startsWith("/classes/") ||
    pathname.startsWith("/join/")
  );
}

const ROLES = new Set(["individual", "teacher", "student"]);

export async function middleware(request: NextRequest) {
  // Auth disabled: behave exactly as before.
  if (!supabaseConfigured()) return NextResponse.next();

  let response = NextResponse.next({ request });
  const supabase = createServerClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        for (const { name, value } of cookiesToSet) {
          request.cookies.set(name, value);
        }
        response = NextResponse.next({ request });
        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options);
        }
      },
    },
  });

  // Refreshes the session cookie; must run before any redirect decision.
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const { pathname, search } = request.nextUrl;
  if (!user && isProtected(pathname)) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = `?next=${encodeURIComponent(pathname + search)}`;
    const redirect = NextResponse.redirect(url);
    // Carry any refreshed/cleared auth cookies onto the redirect.
    for (const cookie of response.cookies.getAll()) {
      redirect.cookies.set(cookie);
    }
    return redirect;
  }
  // Signed in but no role chosen yet (Google sign-in, older accounts): ask once.
  const role = (user?.user_metadata as Record<string, unknown> | undefined)?.role;
  if (user && !ROLES.has(String(role)) && isProtected(pathname)) {
    const url = request.nextUrl.clone();
    url.pathname = "/onboarding";
    url.search = `?next=${encodeURIComponent(pathname + search)}`;
    const redirect = NextResponse.redirect(url);
    for (const cookie of response.cookies.getAll()) {
      redirect.cookies.set(cookie);
    }
    return redirect;
  }
  return response;
}

export const config = {
  matcher: [
    // Everything except API routes, auth routes, Next internals and static files.
    "/((?!api/|auth/|_next/static|_next/image|favicon.ico|.*\.(?:svg|png|jpg|jpeg|gif|webp|ico|mp4|pdf|css|js|map|txt|woff2?)$).*)",
  ],
};
