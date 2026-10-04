"use client";

import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";

type Phase = "idle" | "uploading" | "processing" | "done" | "error";

interface Result {
  document_id: string;
  page_count: number;
  chunk_count: number;
  scanned_pages: number;
  deduplicated: boolean;
}

export function UploadDropzone() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [pct, setPct] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);

  const upload = useCallback(
    (file: File) => {
      if (file.type && file.type !== "application/pdf") {
        setPhase("error");
        setMessage("Only PDF files are supported.");
        return;
      }
      setPhase("uploading");
      setPct(0);
      setMessage(null);
      setResult(null);

      const form = new FormData();
      form.append("file", file);
      form.append("title", file.name.replace(/\.pdf$/i, ""));

      // XHR rather than fetch: fetch still has no upload-progress event, and a
      // progress bar that jumps 0 -> 100 is indistinguishable from a hang on a
      // large scan.
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/documents");
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          const p = e.loaded / e.total;
          setPct(p);
          // Bytes are only the transfer. Once they land, the server still has
          // to parse, chunk and embed -- which is most of the wall clock.
          if (p >= 1) setPhase("processing");
        }
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          const body = JSON.parse(xhr.responseText || "{}") as Result;
          setResult(body);
          setPhase("done");
          router.refresh();
        } else {
          setPhase("error");
          let detail = `Upload failed (${xhr.status}).`;
          try {
            const b = JSON.parse(xhr.responseText || "{}");
            if (b.detail) detail = String(b.detail);
            else if (b.error === "ONLY_PDF") detail = "Only PDF files are supported.";
          } catch {
            /* keep the status-based message */
          }
          setMessage(detail);
        }
      };
      xhr.onerror = () => {
        setPhase("error");
        setMessage("Network error during upload.");
      };
      xhr.send(form);
    },
    [router],
  );

  const busy = phase === "uploading" || phase === "processing";

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const f = e.dataTransfer.files?.[0];
          if (f) upload(f);
        }}
        className={`rounded-lg border border-dashed p-10 text-center transition-colors duration-[120ms] ${
          dragging ? "border-primary bg-primary-subtle" : "border-border-strong bg-surface"
        }`}
      >
        <p className="text-title-sm text-foreground-strong">
          {busy ? "Working…" : "Drop a PDF here"}
        </p>
        <p className="text-ui-sm mt-1 text-muted-foreground">
          Parsed, chunked and embedded on ingest. Up to 50 MB.
        </p>

        <input
          ref={inputRef}
          type="file"
          accept="application/pdf"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload(f);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
          className="text-ui mt-5 inline-flex h-9 items-center rounded-md bg-primary px-3.5 font-medium text-primary-foreground transition-colors duration-[120ms] hover:bg-primary-hover disabled:opacity-60"
        >
          Choose a file
        </button>

        {busy && (
          <div className="mx-auto mt-6 max-w-sm">
            <div className="h-1.5 overflow-hidden rounded-full bg-surface-sunken">
              <div
                className="h-full rounded-full bg-primary transition-[width] duration-200"
                // During processing the real progress is unknown, so the bar is
                // pinned full and the LABEL carries the truth. Faking movement
                // here would be inventing a number the user might check.
                style={{ width: `${phase === "processing" ? 100 : pct * 100}%` }}
              />
            </div>
            <p className="text-caption tnum mt-2 text-muted-foreground">
              {phase === "uploading"
                ? `Uploading ${Math.round(pct * 100)}%`
                : "Parsing, chunking and embedding…"}
            </p>
          </div>
        )}
      </div>

      {phase === "done" && result && (
        <div className="mt-4 rounded-md border border-success bg-success-subtle px-4 py-3">
          <p className="text-ui-sm text-success">
            {result.deduplicated ? (
              <>
                Already indexed &mdash; identical content hash, so nothing was
                re-processed.
              </>
            ) : (
              <>
                Indexed <span className="tnum font-mono">{result.page_count}</span> pages into{" "}
                <span className="tnum font-mono">{result.chunk_count}</span> chunks
                {result.scanned_pages > 0 && (
                  <>
                    {" "}
                    (<span className="tnum font-mono">{result.scanned_pages}</span> scanned)
                  </>
                )}
                .
              </>
            )}
          </p>
        </div>
      )}

      {phase === "error" && (
        <div role="alert" className="mt-4 rounded-md border border-danger bg-danger-subtle px-4 py-3">
          <p className="text-ui-sm text-danger">{message}</p>
        </div>
      )}
    </div>
  );
}
