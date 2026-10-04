import "server-only";
import { SignJWT } from "jose";
import type { Session } from "next-auth";

/**
 * The single chokepoint through which every FastAPI call passes.
 *
 * The browser never reaches the RAG service. Next.js verifies the session,
 * mints a short-lived token carrying the VERIFIED user id, and forwards it.
 * A client-supplied user or org id is never trusted by the backend.
 */

const SECRET = new TextEncoder().encode(
  process.env.RAG_SERVICE_SECRET ?? "change-me",
);
const BASE = process.env.RAG_API_URL ?? "http://localhost:8010";

export async function mintServiceToken(session: Session): Promise<string> {
  return new SignJWT({ org: session.user.orgId, role: session.user.role })
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setIssuer("ekis-web")
    .setAudience("ekis-api")
    .setSubject(session.user.id)
    .setIssuedAt()
    // Deliberately short. This token never leaves the server, so there is no
    // reason for it to outlive the request it was minted for.
    .setExpirationTime("120s")
    .sign(SECRET);
}

export async function ragFetch(
  path: string,
  session: Session,
  init: RequestInit = {},
  { timeoutMs = 30_000 }: { timeoutMs?: number } = {},
): Promise<Response> {
  const token = await mintServiceToken(session);
  return fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init.headers ?? {}),
      authorization: `Bearer ${token}`,
    },
    cache: "no-store",
    signal: init.signal ?? AbortSignal.timeout(timeoutMs),
  });
}

/** JSON helper that preserves the upstream status rather than flattening it. */
export async function ragJson<T>(
  path: string,
  session: Session,
  init: RequestInit = {},
  opts?: { timeoutMs?: number },
): Promise<{ ok: boolean; status: number; data: T | null; error?: string }> {
  try {
    const res = await ragFetch(path, session, init, opts);
    const text = await res.text();
    let data: T | null = null;
    try {
      data = text ? (JSON.parse(text) as T) : null;
    } catch {
      // Non-JSON upstream response; keep the status, drop the body.
    }
    return { ok: res.ok, status: res.status, data };
  } catch (err) {
    // The RAG service being down must not render as a crash.
    return {
      ok: false,
      status: 503,
      data: null,
      error: err instanceof Error ? err.message : "upstream unreachable",
    };
  }
}

export interface DocumentRow {
  id: string;
  title: string;
  filename: string;
  page_count: number | null;
  status: string;
  scanned_pages: number;
  tags: string[];
  created_at: string;
  chunk_count: number;
}
