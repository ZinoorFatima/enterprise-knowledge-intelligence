export const metadata = {
  title: "Documentation",
  description: "Run the stack locally, ingest a document, and ask a question.",
};

function Code({ children }: { children: string }) {
  return (
    <pre className="mt-3 overflow-x-auto rounded-lg border border-border bg-surface-sunken px-4 py-3">
      <code className="text-ui-sm font-mono text-foreground">{children}</code>
    </pre>
  );
}

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-24 border-t border-border py-10">
      <h2 className="text-display-3 text-foreground-strong">{title}</h2>
      <div className="mt-4 max-w-[76ch]">{children}</div>
    </section>
  );
}

export default function DocsPage() {
  return (
    <div className="mx-auto max-w-[1120px] px-6 py-20">
      <p className="text-overline text-muted-foreground">Documentation</p>
      <h1 className="text-display-2 mt-3 text-foreground-strong">Quickstart</h1>
      <p className="text-body-lg mt-5 max-w-[66ch] text-muted-foreground">
        Three services: Postgres with pgvector, a Python retrieval API, and a
        Next.js web app. Everything except the language model runs locally.
      </p>

      <Section id="requirements" title="Requirements">
        <ul className="text-body space-y-2 text-muted-foreground">
          <li>
            <strong className="text-foreground">PostgreSQL 16+ with pgvector 0.8+</strong>{" "}
            &mdash; 0.8 is required for <code className="font-mono">halfvec</code> and
            iterative index scans. Earlier builds will fail the startup check rather
            than degrade silently.
          </li>
          <li>
            <strong className="text-foreground">Python 3.11+</strong> for the retrieval API.
          </li>
          <li>
            <strong className="text-foreground">Node 20+</strong> for the web app.
          </li>
          <li>
            A language-model API key, or{" "}
            <code className="font-mono">LLM_MODE=offline</code> to run the entire
            retrieval half with no key at all.
          </li>
        </ul>
      </Section>

      <Section id="database" title="Database">
        <p className="text-body text-muted-foreground">
          The schema is split across two namespaces so two migration tools can
          share one database without fighting: <code className="font-mono">auth</code>{" "}
          is owned by the web app, <code className="font-mono">rag</code> by the
          Python service. Neither touches the other.
        </p>
        <Code>{`psql -f db/init/00-init.sql          # extensions + schemas
npm --prefix web run db:push         # auth tables (Drizzle)
psql -f api/alembic/sql/0001_rag_schema.sql
psql -f api/alembic/sql/0002_auth_fks.sql   # after auth exists`}</Code>
      </Section>

      <Section id="run" title="Running it">
        <Code>{`# retrieval API
cd api && uvicorn app.main:app --port 8010

# web app
cd web && npm run dev`}</Code>
        <p className="text-ui-sm mt-4 text-muted-foreground">
          Check <code className="font-mono">GET /readyz</code> before anything
          else &mdash; it reports Postgres, the detected pgvector version and the
          indexed chunk count separately, so a degraded component is visible
          rather than collapsing into one boolean.
        </p>
      </Section>

      <Section id="ingest" title="Ingesting documents">
        <p className="text-body text-muted-foreground">
          Upload through the library screen, or seed a development corpus:
        </p>
        <Code>{`python api/scripts/seed_dev.py --bulk 90`}</Code>
        <p className="text-ui-sm mt-4 text-muted-foreground">
          Seeding runs the real pipeline rather than inserting rows directly, so
          seeded documents have genuine page spines, chunk offsets and
          tsvectors &mdash; which is what makes the query-plan tests meaningful.
        </p>
      </Section>

      <Section id="embeddings" title="Embeddings">
        <p className="text-body text-muted-foreground">
          Embeddings from different models are not comparable. A corpus must be
          embedded by exactly one model, or the vector lane silently ranks across
          two incompatible spaces and returns noise. After changing{" "}
          <code className="font-mono">EMBED_MODEL</code>, re-embed everything:
        </p>
        <Code>{`python api/scripts/reembed.py --batch 32`}</Code>
        <p className="text-ui-sm mt-4 text-muted-foreground">
          On CPU this is slow &mdash; roughly 0.1 chunks per second for a
          560M-parameter encoder. Plan for a GPU, a smaller model, or an
          overnight run on a real corpus.
        </p>
      </Section>

      <Section id="evaluation" title="Evaluation">
        <p className="text-body text-muted-foreground">
          The harness calls the same graph the product serves. If measurement
          used a reimplementation, its numbers would describe a system nobody
          ships.
        </p>
        <Code>{`python api/scripts/run_eval.py eval/golden/smoke.jsonl`}</Code>
        <p className="text-ui-sm mt-4 text-muted-foreground">
          Runs against a dataset with unreviewed items, or in offline mode, are
          stamped <code className="font-mono">provisional</code> with the reason
          attached. Metrics that nothing computed report{" "}
          <em>not measured</em> rather than a number.
        </p>
      </Section>

      <Section id="troubleshooting" title="Troubleshooting">
        <dl className="space-y-5">
          <div>
            <dt className="text-title-sm text-foreground-strong">
              Retrieval is slow and gets slower as the corpus grows
            </dt>
            <dd className="text-ui-sm mt-1.5 text-muted-foreground">
              The vector lane is probably not using its index. Run{" "}
              <code className="font-mono">pytest tests/test_hybrid_plan.py</code>
              , which asserts the index is reachable and that the scan stays
              bounded. Wrapping the lane&rsquo;s ordering in a window function is
              the usual cause and produces no error at all.
            </dd>
          </div>
          <div>
            <dt className="text-title-sm text-foreground-strong">
              Vector results look random
            </dt>
            <dd className="text-ui-sm mt-1.5 text-muted-foreground">
              Check that every indexed chunk was embedded by the same model. A
              corpus holding two embedding generations will rank noise above
              real matches.
            </dd>
          </div>
          <div>
            <dt className="text-title-sm text-foreground-strong">
              The first question hangs
            </dt>
            <dd className="text-ui-sm mt-1.5 text-muted-foreground">
              A model is downloading inside the request. Pre-fetch weights, or
              leave <code className="font-mono">RERANK_BACKEND=lexical</code>,
              which needs no model at all.
            </dd>
          </div>
        </dl>
      </Section>
    </div>
  );
}
