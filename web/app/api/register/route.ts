import { hash } from "@node-rs/argon2";
import { eq, sql } from "drizzle-orm";

import { db } from "@/lib/db";
import { memberships, organizations, users } from "@/lib/db/schema";
import { registerSchema } from "@/lib/validation";
import { checkRateLimit } from "@/lib/rate-limit";

export const runtime = "nodejs"; // argon2 is a native binary

// OWASP minimum for argon2id.
const ARGON = { memoryCost: 19456, timeCost: 2, parallelism: 1 } as const;

export async function POST(req: Request) {
  const ip = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "unknown";
  const allowed = await checkRateLimit(`register:${ip}`, { limit: 5, windowSeconds: 900 });
  if (!allowed) {
    return Response.json({ error: "TOO_MANY_ATTEMPTS" }, { status: 429 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "INVALID_JSON" }, { status: 400 });
  }

  const parsed = registerSchema.safeParse(body);
  if (!parsed.success) {
    return Response.json(
      { error: "INVALID_REQUEST", issues: parsed.error.issues },
      { status: 400 },
    );
  }
  const { name, email, password } = parsed.data;

  const [existing] = await db
    .select({ id: users.id })
    .from(users)
    .where(eq(sql`lower(${users.email})`, email.toLowerCase()))
    .limit(1);

  // Deliberately generic: confirming that an address is registered is an
  // account-enumeration oracle.
  if (existing) {
    return Response.json({ error: "COULD_NOT_CREATE" }, { status: 409 });
  }

  const passwordHash = await hash(password, ARGON);

  try {
    await db.transaction(async (tx) => {
      const [user] = await tx
        .insert(users)
        .values({ name, email, passwordHash })
        .returning();
      const [org] = await tx
        .insert(organizations)
        .values({
          name: `${name} workspace`,
          slug: `${name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${user.id.slice(0, 8)}`,
        })
        .returning();
      await tx
        .insert(memberships)
        .values({ userId: user.id, orgId: org.id, role: "owner" });
      await tx.update(users).set({ defaultOrgId: org.id }).where(eq(users.id, user.id));
    });
  } catch {
    // Unique index on lower(email) can still fire under a race.
    return Response.json({ error: "COULD_NOT_CREATE" }, { status: 409 });
  }

  return Response.json({ ok: true }, { status: 201 });
}
