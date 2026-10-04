export const metadata = {
  title: "About",
  description: "Why this exists and what it refuses to do.",
};

export default function AboutPage() {
  return (
    <div className="mx-auto max-w-[1120px] px-6 py-20">
      <p className="text-overline text-muted-foreground">About</p>
      <h1 className="text-display-2 mt-3 max-w-[22ch] text-foreground-strong">
        Built around one question: can you check it?
      </h1>

      <div className="mt-10 max-w-[70ch] space-y-5">
        <p className="text-body-lg text-muted-foreground">
          Most retrieval systems are a black box. A question goes in, a confident
          paragraph comes out, and nothing in between is inspectable. That is
          fine for a demo and unacceptable anywhere a wrong answer has
          consequences.
        </p>
        <p className="text-body-lg text-muted-foreground">
          This system is built on the opposite premise. The rewritten query, both
          retrieval lanes, the fusion, every rerank movement, the exact cited
          spans and a per-claim audit of the answer are all surfaced in the
          product &mdash; not as a debug mode, but as the primary interface.
        </p>
      </div>

      <section className="mt-16">
        <h2 className="text-display-3 text-foreground-strong">Positions we hold</h2>
        <dl className="mt-8 grid gap-x-12 gap-y-8 md:grid-cols-2">
          <div>
            <dt className="text-title-sm text-foreground-strong">
              A refusal is a good answer
            </dt>
            <dd className="text-body mt-2 text-muted-foreground">
              When nothing retrieved clears the relevance floors, the model gets
              no evidence and says so. Padding the context to a fixed size is how
              &ldquo;I don&rsquo;t know&rdquo; turns into a confident fabrication.
            </dd>
          </div>
          <div>
            <dt className="text-title-sm text-foreground-strong">
              An unmeasured number is not a number
            </dt>
            <dd className="text-body mt-2 text-muted-foreground">
              Metrics nothing computed display as <em>not measured</em>. Runs
              against unreviewed data are stamped provisional with the reason
              attached. A dashboard showing 0.94 for something no code evaluated
              is worse than an empty dashboard.
            </dd>
          </div>
          <div>
            <dt className="text-title-sm text-foreground-strong">
              A model should not grade itself
            </dt>
            <dd className="text-body mt-2 text-muted-foreground">
              Self-grading inflates faithfulness measurably. The verifier is
              always a different model, and the system raises an error rather
              than permitting the alternative.
            </dd>
          </div>
          <div>
            <dt className="text-title-sm text-foreground-strong">
              Never show a confident wrong highlight
            </dt>
            <dd className="text-body mt-2 text-muted-foreground">
              When a scan is too degraded to locate a span precisely, the
              citation says approximate. On a product whose claim is provenance,
              a confidently misplaced highlight is worse than none at all.
            </dd>
          </div>
        </dl>
      </section>

      <section className="mt-16 rounded-xl border border-border bg-surface p-8">
        <h2 className="text-display-3 text-foreground-strong">What it is not</h2>
        <p className="text-body mt-3 max-w-[66ch] text-muted-foreground">
          It is not a general assistant, and it will not answer from the
          model&rsquo;s own knowledge. If the corpus does not contain the answer,
          the correct output is to say so &mdash; and that behaviour is measured
          separately, so a system that quietly starts answering everything shows
          up as a regression rather than as an improvement.
        </p>
      </section>
    </div>
  );
}
