import NextAuth from "next-auth";
import { NextResponse } from "next/server";
import { authConfig } from "./auth.config";

// Edge-safe instance: authConfig has no adapter and no argon2.
const { auth } = NextAuth(authConfig);

const AUTH_PAGES = new Set(["/sign-in", "/sign-up"]);

/**
 * Middleware is NOT the security boundary. It only avoids rendering the app
 * shell for a signed-out visitor. The real guards are the server-side auth()
 * call in app/(app)/layout.tsx and in every route handler -- which is what
 * makes this matcher a performance optimization rather than a correctness
 * dependency.
 */
export default auth((req) => {
  const { nextUrl } = req;
  const signedIn = !!req.auth?.user;
  const path = nextUrl.pathname;

  if (AUTH_PAGES.has(path)) {
    if (!signedIn) return NextResponse.next();
    const next = safeNext(nextUrl.searchParams.get("next"));
    return NextResponse.redirect(new URL(next ?? "/library", nextUrl));
  }

  if (!signedIn) {
    const url = new URL("/sign-in", nextUrl);
    // Preserve the full intended destination, query string included.
    url.searchParams.set("next", path + nextUrl.search);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
});

/** Same-origin absolute paths only. Rejects "//evil.com" and "/\evil.com". */
function safeNext(v: string | null): string | null {
  if (!v) return null;
  if (!v.startsWith("/")) return null;
  if (v.startsWith("//") || v.startsWith("/\\")) return null;
  return v;
}

export const config = {
  // Explicit gated prefixes only, so marketing routes stay fully static and
  // never pay edge latency.
  matcher: [
    "/dashboard/:path*",
    "/library/:path*",
    "/ask/:path*",
    "/eval/:path*",
    "/settings/:path*",
    "/sign-in",
    "/sign-up",
  ],
};
