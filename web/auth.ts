import NextAuth from "next-auth";
import Credentials from "next-auth/providers/credentials";
import { DrizzleAdapter } from "@auth/drizzle-adapter";
import { verify } from "@node-rs/argon2";
import { eq, sql } from "drizzle-orm";

import { authConfig } from "./auth.config";
import { db } from "@/lib/db";
import {
  accounts,
  memberships,
  organizations,
  sessions,
  users,
  verificationTokens,
} from "@/lib/db/schema";
import { credentialsSchema } from "@/lib/validation";

/**
 * NODE ONLY. Imported by route handlers, server components and server actions.
 * Never import this from middleware.ts -- see auth.config.ts.
 */

// A real argon2id hash of a random string. Verifying against this when the user
// does not exist keeps response time constant, so timing cannot be used to
// enumerate which email addresses have accounts.
const DUMMY_HASH =
  "$argon2id$v=19$m=19456,t=2,p=1$c29tZXNhbHR2YWx1ZTEyMw$Yk5nHhVPZ1dYgJ5xqX3xJ8WnJvV9Xq0kK7tR2mN4pQs";

function slugify(name: string, id: string): string {
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return `${base || "workspace"}-${id.slice(0, 8)}`;
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  ...authConfig,
  adapter: DrizzleAdapter(db, {
    usersTable: users,
    accountsTable: accounts,
    sessionsTable: sessions,
    verificationTokensTable: verificationTokens,
  }),
  providers: [
    ...authConfig.providers,
    Credentials({
      credentials: { email: {}, password: {} },
      async authorize(raw) {
        const parsed = credentialsSchema.safeParse(raw);
        if (!parsed.success) return null;
        const { email, password } = parsed.data;

        const [u] = await db
          .select()
          .from(users)
          .where(eq(sql`lower(${users.email})`, email.toLowerCase()))
          .limit(1);

        // Always do the hash work, even for a non-existent user.
        const hash = u?.passwordHash ?? DUMMY_HASH;
        let ok = false;
        try {
          ok = await verify(hash, password);
        } catch {
          ok = false;
        }
        if (!u || !u.passwordHash || !ok) return null;

        const [m] = await db
          .select()
          .from(memberships)
          .where(eq(memberships.userId, u.id))
          .limit(1);

        return {
          id: u.id,
          email: u.email,
          name: u.name,
          image: u.image,
          orgId: m?.orgId,
          role: m?.role ?? "member",
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
        } as any;
      },
    }),
  ],
  callbacks: {
    ...authConfig.callbacks,
    async signIn({ account, profile }) {
      if (account?.provider === "google") {
        // Only adopt an existing account when Google asserts the address is
        // verified. Without this, email ownership is unproven and account
        // takeover is possible.
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        if (!(profile as any)?.email_verified) return false;
      }
      return true;
    },
  },
  events: {
    async createUser({ user }) {
      // Every user gets a personal organization, so ownership rows in rag.*
      // always have a non-null org_id to foreign-key against.
      if (!user.id) return;
      await db.transaction(async (tx) => {
        const [org] = await tx
          .insert(organizations)
          .values({
            name: `${user.name ?? user.email ?? "Personal"} workspace`,
            slug: slugify(user.name ?? user.email ?? "workspace", user.id!),
          })
          .returning();
        await tx
          .insert(memberships)
          .values({ userId: user.id!, orgId: org.id, role: "owner" });
        await tx
          .update(users)
          .set({ defaultOrgId: org.id })
          .where(eq(users.id, user.id!));
      });
    },
  },
});
