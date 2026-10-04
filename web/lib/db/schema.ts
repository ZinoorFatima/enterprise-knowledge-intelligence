import {
  index,
  integer,
  pgSchema,
  primaryKey,
  text,
  timestamp,
  uniqueIndex,
  uuid,
} from "drizzle-orm/pg-core";
import { sql } from "drizzle-orm";
import type { AdapterAccountType } from "next-auth/adapters";

/**
 * The `auth` schema is owned by Next.js / Drizzle Kit. The `rag` schema is
 * owned by FastAPI / Alembic. Neither migration tool touches the other's
 * schema (drizzle.config.ts sets schemaFilter: ['auth']).
 *
 * rag.documents.org_id references auth.organizations.id, which is what lets the
 * Python side enforce row-level ownership in plain SQL with no RPC round trip.
 */
export const authSchema = pgSchema("auth");

export const orgRole = authSchema.enum("org_role", [
  "owner",
  "admin",
  "member",
  "viewer",
]);

export const organizations = authSchema.table("organizations", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: text("name").notNull(),
  slug: text("slug").notNull().unique(),
  plan: text("plan").notNull().default("free"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

export const users = authSchema.table(
  "users",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    name: text("name"),
    email: text("email").notNull(),
    emailVerified: timestamp("email_verified", { withTimezone: true, mode: "date" }),
    image: text("image"),
    // NULL for OAuth-only users. Never compare a submitted password against
    // NULL -- see the constant-work path in auth.ts.
    passwordHash: text("password_hash"),
    // Denormalized so a JWT can be minted without a join on every request.
    defaultOrgId: uuid("default_org_id"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [
    // Case-insensitive uniqueness: Alice@x.com and alice@x.com are one account.
    uniqueIndex("users_email_lower_idx").on(sql`lower(${t.email})`),
  ],
);

export const memberships = authSchema.table(
  "memberships",
  {
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    orgId: uuid("org_id")
      .notNull()
      .references(() => organizations.id, { onDelete: "cascade" }),
    role: orgRole("role").notNull().default("member"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [
    primaryKey({ columns: [t.userId, t.orgId] }),
    index("memberships_org_idx").on(t.orgId),
  ],
);

export const accounts = authSchema.table(
  "accounts",
  {
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    type: text("type").$type<AdapterAccountType>().notNull(),
    provider: text("provider").notNull(),
    providerAccountId: text("provider_account_id").notNull(),
    refresh_token: text("refresh_token"),
    access_token: text("access_token"),
    expires_at: integer("expires_at"),
    token_type: text("token_type"),
    scope: text("scope"),
    id_token: text("id_token"),
    session_state: text("session_state"),
  },
  (t) => [primaryKey({ columns: [t.provider, t.providerAccountId] })],
);

/**
 * Unused at runtime (the session strategy is JWT), but the adapter's type
 * contract requires it -- and it is the migration path if server-side
 * revocation is ever needed.
 */
export const sessions = authSchema.table("sessions", {
  sessionToken: text("session_token").primaryKey(),
  userId: uuid("user_id")
    .notNull()
    .references(() => users.id, { onDelete: "cascade" }),
  expires: timestamp("expires", { withTimezone: true, mode: "date" }).notNull(),
});

export const verificationTokens = authSchema.table(
  "verification_tokens",
  {
    identifier: text("identifier").notNull(),
    token: text("token").notNull(),
    expires: timestamp("expires", { withTimezone: true, mode: "date" }).notNull(),
  },
  (t) => [primaryKey({ columns: [t.identifier, t.token] })],
);

/**
 * Rate limiting in Postgres rather than Redis: one fewer service to run, and
 * sign-in attempts are low-volume by nature.
 */
export const rateLimits = authSchema.table(
  "rate_limits",
  {
    key: text("key").primaryKey(),
    count: integer("count").notNull().default(0),
    windowStart: timestamp("window_start", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [index("rate_limits_window_idx").on(t.windowStart)],
);
