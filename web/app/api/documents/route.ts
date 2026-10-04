import { auth } from "@/auth";
import { ragFetch, ragJson, type DocumentRow } from "@/lib/rag-client";

// Node runtime: jose and the upstream fetch both need it, and the RAG service
// is not reachable from the Edge network.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const session = await auth();
  if (!session?.user) {
    return Response.json({ error: "UNAUTHENTICATED" }, { status: 401 });
  }

  const res = await ragJson<{ items: DocumentRow[] }>("/v1/documents", session);
  if (!res.ok) {
    return Response.json(
      { error: "UPSTREAM_ERROR", status: res.status, detail: res.error },
      { status: res.status >= 500 ? 502 : res.status },
    );
  }
  return Response.json(res.data ?? { items: [] });
}

export async function POST(req: Request) {
  const session = await auth();
  if (!session?.user) {
    return Response.json({ error: "UNAUTHENTICATED" }, { status: 401 });
  }

  const form = await req.formData();
  const file = form.get("file");
  if (!(file instanceof File)) {
    return Response.json({ error: "NO_FILE" }, { status: 400 });
  }
  if (file.type && file.type !== "application/pdf") {
    return Response.json({ error: "ONLY_PDF" }, { status: 415 });
  }

  // Rebuild the multipart body rather than forwarding the parsed one, so the
  // upstream sees a clean request with only the fields we intend to send.
  const upstream = new FormData();
  upstream.append("file", file, file.name);
  const title = form.get("title");
  if (typeof title === "string" && title.trim()) upstream.append("title", title.trim());
  const tags = form.get("tags");
  if (typeof tags === "string" && tags.trim()) upstream.append("tags", tags.trim());

  // Ingestion runs OCR, chunking and embedding inline; a 30s default would cut
  // a legitimate upload off mid-pipeline.
  const res = await ragFetch(
    "/v1/documents",
    session,
    { method: "POST", body: upstream },
    { timeoutMs: 300_000 },
  );

  const text = await res.text();
  return new Response(text || "{}", {
    status: res.status,
    headers: { "content-type": "application/json" },
  });
}
