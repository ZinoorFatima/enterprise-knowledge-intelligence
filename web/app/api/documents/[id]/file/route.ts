import { auth } from "@/auth";
import { ragFetch } from "@/lib/rag-client";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Streams the original PDF through the trust boundary.
 *
 * Range headers are forwarded in both directions and the 206 status is
 * preserved. Collapsing a range request to a 200 would make pdf.js download the
 * whole file to show one page, which on a 600-page scan is the difference
 * between instant and unusable.
 */
export async function GET(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const session = await auth();
  if (!session?.user) {
    return Response.json({ error: "UNAUTHENTICATED" }, { status: 401 });
  }

  const { id } = await params;
  if (!/^[0-9a-f-]{36}$/i.test(id)) {
    return Response.json({ error: "INVALID_ID" }, { status: 400 });
  }

  const range = req.headers.get("range");
  const upstream = await ragFetch(
    `/v1/documents/${id}/file`,
    session,
    { headers: range ? { range } : {} },
    { timeoutMs: 60_000 },
  );

  if (!upstream.ok && upstream.status !== 206) {
    const detail = await upstream.text().catch(() => "");
    return new Response(detail || JSON.stringify({ error: "UPSTREAM_ERROR" }), {
      status: upstream.status,
      headers: { "content-type": "application/json" },
    });
  }

  const headers = new Headers();
  for (const h of [
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "content-disposition",
    "cache-control",
  ]) {
    const v = upstream.headers.get(h);
    if (v) headers.set(h, v);
  }

  return new Response(upstream.body, { status: upstream.status, headers });
}
