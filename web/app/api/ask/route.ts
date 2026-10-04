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
      date_from: z.string().optional(),
      date_to: z.string().optional(),
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

  const res = await ragFetch(
    "/v1/ask",
    session,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(parsed.data),
      // Propagate client aborts upstream. Without this, pressing Stop is
      // cosmetic and the backend keeps working (and, in live mode, spending).
      signal: req.signal,
    },
    { timeoutMs: 120_000 },
  );

  const text = await res.text();
  return new Response(text || "{}", {
    status: res.status,
    headers: { "content-type": "application/json" },
  });
}
