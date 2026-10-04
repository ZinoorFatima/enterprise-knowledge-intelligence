import { auth } from "@/auth";
import { ragJson } from "@/lib/rag-client";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const session = await auth();
  if (!session?.user) {
    return Response.json({ error: "UNAUTHENTICATED" }, { status: 401 });
  }
  const res = await ragJson("/v1/eval/datasets", session);
  return Response.json(res.data ?? { items: [] }, { status: res.ok ? 200 : 502 });
}

export async function POST(req: Request) {
  const session = await auth();
  if (!session?.user) {
    return Response.json({ error: "UNAUTHENTICATED" }, { status: 401 });
  }
  // Running an eval exercises the whole pipeline for every item; in live mode
  // that is real model spend, so it is an owner/admin action upstream.
  const { dataset = "smoke" } = await req.json().catch(() => ({}));
  if (typeof dataset !== "string" || !/^[\w.-]{1,64}$/.test(dataset)) {
    return Response.json({ error: "INVALID_DATASET" }, { status: 400 });
  }

  const res = await ragJson(
    `/v1/eval/runs?dataset=${encodeURIComponent(dataset)}`,
    session,
    { method: "POST" },
    // An eval run walks the full graph per item; the default 30s would abort a
    // legitimate run partway through.
    { timeoutMs: 600_000 },
  );
  return Response.json(res.data ?? { error: "UPSTREAM_ERROR", detail: res.error }, {
    status: res.ok ? 200 : res.status,
  });
}
