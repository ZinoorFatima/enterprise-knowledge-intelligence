import Link from "next/link";
import { PipelineDiagram } from "@/components/marketing/pipeline-diagram";

/**
 * Landing page. Zero data fetching, one hydrated island (the diagram).
 *
 * Deliberately absent: testimonial carousel, fake logo wall, animated gradient
 * blobs, "trusted by 10,000+ teams", chat bubble, email popup. The argument is
 * made with engineering evidence, because the buyer for this has already been
 * burned by a demo that could not show its work.
 */
export default function LandingPage() {
  return (
    <>
      {/* ── Hero ───────────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-[1120px] px-6 pt-20 pb-24">
        <div className="max-w-[760px]">
          <p className="text-overline text-muted-foreground">
            Retrieval-augmented generation for regulated teams
          </p>
          <h1 className="text-display-1 mt-4 text-foreground-strong max-lg:text-[44px] max-lg:leading-[48px] max-sm:text-[36px] max-sm:leading-[40px]">
            Answers your auditors can check.
          </h1>
          <p className="text-body-lg mt-5 max-w-[58ch] text-muted-foreground">
            Ask questions across ten thousand contracts, filings and scanned
            PDFs. Every sentence carries a citation to the exact page and span it
            came from &mdash; and a verifier that tells you when it can&rsquo;t
            find support.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Link
              href="/sign-up"
              className="text-ui inline-flex h-10 items-center rounded-md bg-primary px-4 font-medium text-primary-foreground transition-colors duration-[120ms] hover:bg-primary-hover"
            >
              Start free
            </Link>
            <Link
              href="/evaluation"
              className="text-ui inline-flex h-10 items-center rounded-md px-3 font-medium text-foreground transition-colors duration-[120ms] hover:bg-surface"
            >
              See the evaluation &rarr;
            </Link>
          </div>

          <dl className="mt-12 flex flex-wrap gap-x-10 gap-y-4 border-t border-border pt-6">
            <Stat label="Claims mapped to a source span" value="100%" />
            <Stat label="Retrieval lanes, fused" value="2" />
            <Stat label="Verified per answer" value="every claim" />
          </dl>
        </div>
      </section>

      {/* ── The problem, as a comparison ───────────────────────────────── */}
      <section className="border-y border-border bg-surface">
        <div className="mx-auto max-w-[1120px] px-6 py-24">
          <h2 className="text-display-3 max-w-[24ch] text-foreground-strong">
            Most RAG demos cannot show their work.
          </h2>
          <div className="mt-10 grid gap-5 md:grid-cols-2">
            <Panel
              tone="danger"
              badge="Unverifiable"
              title="What most demos do"
              body="The indemnity cap is twelve months of fees, and it resets annually on the renewal date."
              note="No citation. No way to tell which half is true. You find out in the deposition."
            />
            <Panel
              tone="success"
              badge="8 of 8 claims supported"
              title="What this does"
              body="The indemnity cap is twelve months of fees [1], except for claims arising from IP infringement [2]."
              note="Each marker opens the source page with the cited sentence highlighted. Unsupported claims are underlined in the answer itself."
            />
          </div>
        </div>
      </section>

      {/* ── Pipeline ───────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-[1120px] px-6 py-24">
        <p className="text-overline text-muted-foreground">How it works</p>
        <h2 className="text-display-2 mt-3 text-foreground-strong">
          Ten stages, all inspectable.
        </h2>
        <p className="text-body-lg mt-4 max-w-[64ch] text-muted-foreground">
          Retrieval is not a black box in this system. The rewritten query, both
          search lanes, the fusion, the rerank scores and the cited spans are all
          visible in the product.
        </p>
        <PipelineDiagram className="mt-12" />
        <Link
          href="/how-it-works"
          className="text-ui mt-6 inline-flex font-medium text-primary hover:underline underline-offset-4"
        >
          Read the full pipeline &rarr;
        </Link>
      </section>

      {/* ── Three features that competitors' screenshots don't show ────── */}
      <section className="border-t border-border">
        <div className="mx-auto max-w-[1120px] space-y-24 px-6 py-24">
          <Feature
            eyebrow="Retrieval inspector"
            title="See both lanes, and why they disagreed."
            body="Keyword search finds the clause number. Vector search finds the paraphrase. The inspector shows each lane's rank, what the fusion did, and how far the reranker moved every result — with a plain statement that BM25 and cosine scores are not comparable."
          />
          <Feature
            eyebrow="Citations"
            title="Click a marker, land on page 47."
            body="Citations resolve to a character span, not a document. The viewer scrolls to the page and highlights the exact sentence. When a scan is too poor to locate the span precisely, it says so instead of highlighting the wrong thing."
          />
          <Feature
            eyebrow="Verification"
            title="Per-claim entailment, not a confidence score."
            body="The answer is decomposed into atomic claims and each is checked against the retrieved evidence by a different model than the one that wrote it. Unsupported sentences are flagged in the answer, and the verifier's own measured accuracy is shown next to the score."
          />
        </div>
      </section>

      {/* ── Deployment / trust ─────────────────────────────────────────── */}
      <section className="border-t border-border bg-surface">
        <div className="mx-auto max-w-[1120px] px-6 py-24">
          <p className="text-overline text-muted-foreground">Deployment</p>
          <h2 className="text-display-3 mt-3 max-w-[26ch] text-foreground-strong">
            Your documents never leave your deployment.
          </h2>
          <ul className="mt-8 grid gap-x-10 gap-y-4 sm:grid-cols-2">
            {[
              "Parsing, OCR, embeddings and reranking all run locally — no document text is sent to an embedding API.",
              "The browser never reaches the retrieval service; every call is proxied through a session-verified handler.",
              "Single-tenant deploy via docker compose. Postgres holds vectors and full text in one database.",
              "Every query stores the exact chunks it retrieved, so an audit can reconstruct any answer.",
            ].map((t) => (
              <li key={t} className="text-body flex gap-3 text-muted-foreground">
                <span aria-hidden className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                {t}
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* ── Final CTA ──────────────────────────────────────────────────── */}
      <section className="mx-auto max-w-[1120px] px-6 py-24">
        <h2 className="text-display-2 max-w-[20ch] text-foreground-strong">
          Answers your auditors can check.
        </h2>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link
            href="/sign-up"
            className="text-ui inline-flex h-10 items-center rounded-md bg-primary px-4 font-medium text-primary-foreground transition-colors duration-[120ms] hover:bg-primary-hover"
          >
            Start free
          </Link>
          <Link
            href="/evaluation"
            className="text-ui inline-flex h-10 items-center rounded-md border border-border-strong bg-surface-raised px-4 font-medium text-foreground transition-colors duration-[120ms] hover:bg-surface"
          >
            See how it was measured
          </Link>
        </div>
      </section>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-caption text-muted-foreground">{label}</dt>
      <dd className="text-title-lg tnum mt-0.5 font-mono text-foreground-strong">
        {value}
      </dd>
    </div>
  );
}

function Panel({
  tone,
  badge,
  title,
  body,
  note,
}: {
  tone: "danger" | "success";
  badge: string;
  title: string;
  body: string;
  note: string;
}) {
  const fg = tone === "danger" ? "var(--danger)" : "var(--success)";
  const bg = tone === "danger" ? "var(--danger-subtle)" : "var(--success-subtle)";
  return (
    <div className="rounded-xl border border-border bg-surface-raised p-6">
      <div className="flex items-center justify-between gap-4">
        <p className="text-title-sm text-foreground-strong">{title}</p>
        <span
          className="text-caption shrink-0 rounded-full border px-2.5 py-1 font-medium"
          style={{ color: fg, background: bg, borderColor: fg }}
        >
          {badge}
        </span>
      </div>
      <p className="text-body mt-4 text-foreground">{body}</p>
      <p className="text-ui-sm mt-4 border-t border-border pt-4 text-muted-foreground">
        {note}
      </p>
    </div>
  );
}

function Feature({
  eyebrow,
  title,
  body,
}: {
  eyebrow: string;
  title: string;
  body: string;
}) {
  return (
    <div className="grid gap-6 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] md:gap-14">
      <div>
        <p className="text-overline text-muted-foreground">{eyebrow}</p>
        <h3 className="text-display-3 mt-3 text-foreground-strong">{title}</h3>
      </div>
      <p className="text-body-lg self-center text-muted-foreground">{body}</p>
    </div>
  );
}
