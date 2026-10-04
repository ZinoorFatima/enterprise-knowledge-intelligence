import type { NextAuthConfig } from "next-auth";
import Google from "next-auth/providers/google";

/**
 * EDGE SAFE. No adapter, no database driver, no argon2.
 *
 * This split is mandatory, not stylistic: middleware runs on the Edge runtime,
 * where argon2 (native binary) and the Postgres driver (TCP sockets) cannot
 * load. Importing auth.ts into middleware is the classic way this stack breaks
 * -- the build either fails or silently pulls in a polyfill and balloons.
 */
export const authConfig = {
  pages: {
    signIn: "/sign-in",
    error: "/auth-error",
    newUser: "/library?onboarding=1",
  },
  session: {
    strategy: "jwt",
    maxAge: 60 * 60 * 24 * 30, // 30 days
    updateAge: 60 * 60 * 24, // refresh the token at most once a day
  },
  trustHost: true,
  // Credentials is added only in auth.ts, because authorize() needs argon2.
  providers: [
    Google({
      // Linking is done explicitly in the signIn callback, gated on
      // email_verified. The built-in flag skips that check, which would let
      // anyone who can create a Google account with a victim's email string
      // take over an existing credentials account.
      allowDangerousEmailAccountLinking: false,
    }),
  ],
  callbacks: {
    jwt({ token, user, trigger, session }) {
      if (user) {
        token.uid = user.id!;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        token.orgId = (user as any).orgId;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        token.role = (user as any).role ?? "member";
      }
      if (trigger === "update" && session?.orgId) {
        token.orgId = session.orgId;
      }
      return token;
    },
    session({ session, token }) {
      if (session.user) {
        session.user.id = token.uid as string;
        session.user.orgId = token.orgId as string;
        session.user.role = token.role as "owner" | "admin" | "member" | "viewer";
      }
      return session;
    },
  },
} satisfies NextAuthConfig;
