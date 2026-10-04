import "server-only";
import { sql } from "drizzle-orm";
import { db } from "@/lib/db";

/**
 * Fixed-window rate limit in Postgres. No Redis dependency: sign-in and
 * registration are low-volume by nature, and one fewer service to run is worth
 * more here than the precision of a sliding window.
 *
 * Returns true when the request is allowed.
 */
export async function checkRateLimit(
  key: string,
  { limit, windowSeconds }: { limit: number; windowSeconds: number },
): Promise<boolean> {
  try {
    const rows = await db.execute(sql`
      INSERT INTO auth.rate_limits (key, count, window_start)
      VALUES (${key}, 1, now())
      ON CONFLICT (key) DO UPDATE SET
        count = CASE
          WHEN auth.rate_limits.window_start < now() - make_interval(secs => ${windowSeconds})
          THEN 1
          ELSE auth.rate_limits.count + 1
        END,
        window_start = CASE
          WHEN auth.rate_limits.window_start < now() - make_interval(secs => ${windowSeconds})
          THEN now()
          ELSE auth.rate_limits.window_start
        END
      RETURNING count
    `);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const count = Number((rows as any)[0]?.count ?? 0);
    return count <= limit;
  } catch {
    // Fail open: a rate-limiter outage must not lock every user out of signing
    // in. The tradeoff is explicit and deliberate.
    return true;
  }
}
