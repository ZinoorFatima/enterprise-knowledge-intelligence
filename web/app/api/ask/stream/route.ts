import { z } from "zod";
import { auth } from "@/auth";
import { ragFetch } from "@/lib/rag-client";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 120;

const askSchema = z.object({
  question: z.string().trim().min(1).max(4000),
  filters: z
    .object({
      document_ids: z.array(z.string()).max(500).optional(),
      tags: z.array(z.string()).max(50).optional(),
    })
    .optional(),
  rewrite: z.boolean().optional(),
  verify: z.boolean().optional(),
});

export async function POST(req: Request) {
  const session = await auth();
  if (!session?.user) {
    return Response.json({ error: "UNAUTHENTICATED" }, { status: 401 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "INVALID_JSON" }, { status: 400 });
  }
  const parsed = askSchema.safeParse(body);
  if (!parsed.success) {
    return Response.json(
      { error: "INVALID_REQUEST", issues: parsed.error.issues },
      { status: 400 },
    );
  }

  const upstream = await ragFetch(
    "/v1/ask/stream",
    session,
    {
      method: "POST",
      headers: { "content-type": "application/json", accept: "text/event-stream" },
      body: JSON.stringify(parsed.data),
      // Propagating the client abort is what makes "Stop" real rather than
      // cosmetic: without it the backend keeps working, and in live mode keeps
      // spending, after the user has walked away.
      signal: req.signal,
    },
    { timeoutMs: 120_000 },
  );

  if (!upstream.ok || !upstream.body) {
    const detail = await upstream.text().catch(() => "");
    return Response.json(
      { error: "UPSTREAM_ERROR", status: upstream.status, detail },
      { status: upstream.status >= 500 ? 502 : upstream.status },
    );
  }

  // Hand the upstream stream straight through. Every wrapper is somewhere
  // backpressure and cancellation can be silently dropped.
  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-store, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
      "content-encoding": "identity",
    },
  });
}
