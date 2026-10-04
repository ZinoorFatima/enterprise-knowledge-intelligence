import { auth } from "@/auth";
import { ragJson, type DocumentRow } from "@/lib/rag-client";
import { UploadDropzone } from "@/components/app/upload-dropzone";

export const metadata = { title: "Library" };
export const dynamic = "force-dynamic";

export default async function LibraryPage() {
  const session = await auth();
  // The layout guard already redirected an unauthenticated visitor; this is a
  // type narrowing, not a second security check.
  if (!session?.user) return null;

  const res = await ragJson<{ items: DocumentRow[] }>("/v1/documents", session);
  const docs = res.data?.items ?? [];

  return (
    <div className="mx-auto max-w-[1120px] px-6 py-10">
      <div className="flex items-baseline justify-between gap-4">
        <div>
          <h1 className="text-title-lg text-foreground-strong">Document library</h1>
          <p className="text-ui-sm mt-1 text-muted-foreground">
            {docs.length === 0
              ? "No documents yet."
              : `${docs.length} documents · ${docs.reduce((n, d) => n + (d.chunk_count ?? 0), 0)} chunks indexed`}
          </p>
        </div>
      </div>

      {!res.ok && (
        <div role="alert" className="mt-6 rounded-md border border-danger bg-danger-subtle px-4 py-3">
          <p className="text-ui-sm text-danger">
            Could not reach the retrieval service ({res.status}). Uploading is
            unavailable until it is back.
          </p>
        </div>
      )}

      <div className="mt-6">
        <UploadDropzone />
      </div>

      {docs.length > 0 && (
        <div className="mt-8 overflow-hidden rounded-lg border border-border">
          <table className="w-full">
            <thead className="bg-surface-sunken">
              <tr className="text-overline text-muted-foreground">
                <th className="px-4 py-2.5 text-left font-semibold">Document</th>
                <th className="px-4 py-2.5 text-right font-semibold">Pages</th>
                <th className="px-4 py-2.5 text-right font-semibold">Chunks</th>
                <th className="px-4 py-2.5 text-right font-semibold">Scanned</th>
                <th className="px-4 py-2.5 text-left font-semibold">Status</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.id} className="border-t border-border">
                  <td className="px-4 py-2.5">
                    <div className="text-ui text-foreground">{d.title}</div>
                    <div className="text-caption text-subtle-foreground">{d.filename}</div>
                  </td>
                  {/* Tabular mono on every number that gets compared down a column. */}
                  <td className="text-ui-sm tnum px-4 py-2.5 text-right font-mono text-foreground">
                    {d.page_count ?? "—"}
                  </td>
                  <td className="text-ui-sm tnum px-4 py-2.5 text-right font-mono text-foreground">
                    {d.chunk_count}
                  </td>
                  <td className="text-ui-sm tnum px-4 py-2.5 text-right font-mono text-muted-foreground">
                    {d.scanned_pages > 0 ? d.scanned_pages : "—"}
                  </td>
                  <td className="px-4 py-2.5">
                    <StatusPill status={d.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const tone =
    status === "ready"
      ? ["var(--success)", "var(--success-subtle)"]
      : status === "failed"
        ? ["var(--danger)", "var(--danger-subtle)"]
        : ["var(--warning)", "var(--warning-subtle)"];
  return (
    <span
      className="text-caption inline-flex items-center rounded-full border px-2 py-0.5 font-medium"
      style={{ color: tone[0], background: tone[1], borderColor: tone[0] }}
    >
      {status}
    </span>
  );
}
