import Link from "next/link";

export const metadata = {
  title: "Pricing",
  description: "Single-tenant deployment with per-seat and self-hosted options.",
};

const TIERS = [
  {
    name: "Developer",
    price: "Free",
    cadence: "self-hosted",
    blurb: "The full pipeline, running on your own machine.",
    features: [
      "Unlimited documents",
      "Hybrid retrieval, reranking, verification",
      "Local embeddings — no document text leaves the host",
      "Bring your own model API key",
      "Community support",
    ],
    cta: "Clone the repo",
    href: "/docs",
    emphasis: false,
  },
  {
    name: "Team",
    price: "$49",
    cadence: "per seat / month",
    blurb: "Hosted, single-tenant, with the evaluation harness wired to your corpus.",
    features: [
      "Everything in Developer",
      "Managed single-tenant deployment",
      "Golden-set builder and scheduled eval runs",
      "Audit log of every query and retrieved passage",
      "SSO via Google",
      "Email support, next business day",
    ],
    cta: "Start free",
    href: "/sign-up",
    emphasis: true,
  },
  {
    name: "Enterprise",
    price: "Custom",
    cadence: "annual",
    blurb: "Deployed inside your VPC, against your compliance requirements.",
    features: [
      "Everything in Team",
      "Deploy in your own VPC or air-gapped",
      "SAML, SCIM, custom retention",
      "Per-document access controls",
      "Human-labelled verifier calibration on your corpus",
      "Named support engineer",
    ],
    cta: "Talk to us",
    href: "/about",
    emphasis: false,
  },
] as const;

const FAQ = [
  {
    q: "What actually leaves my deployment?",
    a: "Parsing, OCR, embeddings and reranking all run locally — no document text is sent to an embedding provider. Only the assembled context for a question goes to the language model, and on Enterprise you can point that at a model running inside your own network.",
  },
  {
    q: "How is a seat counted?",
    a: "A seat is a person who can sign in and ask questions. Service accounts used for ingestion or scheduled evaluation runs are not seats.",
  },
  {
    q: "Do I pay per document or per query?",
    a: "Neither. Ingestion cost is compute you already own, and query cost is dominated by the language model, which you pay your provider for directly. We do not mark that up.",
  },
  {
    q: "Can I check the quality claims against my own corpus?",
    a: "That is the point of the evaluation harness being part of the product rather than a slide. You build a golden set from your own documents and the same code that serves answers produces the measurements.",
  },
] as const;

export default function PricingPage() {
  return (
    <div className="mx-auto max-w-[1120px] px-6 py-20">
      <p className="text-overline text-muted-foreground">Pricing</p>
      <h1 className="text-display-2 mt-3 max-w-[20ch] text-foreground-strong">
        Priced per seat, not per question.
      </h1>
      <p className="text-body-lg mt-5 max-w-[60ch] text-muted-foreground">
        Retrieval runs on hardware you control. The only usage-metered cost is
        the language model, which you pay your provider for directly.
      </p>

      <div className="mt-14 grid gap-5 lg:grid-cols-3">
        {TIERS.map((t) => (
          <div
            key={t.name}
            className="flex flex-col rounded-xl border bg-surface-raised p-6"
            style={{
              borderColor: t.emphasis ? "var(--primary)" : "var(--border)",
              boxShadow: t.emphasis ? "var(--shadow-md)" : undefined,
            }}
          >
            <div className="flex items-baseline justify-between">
              <h2 className="text-title text-foreground-strong">{t.name}</h2>
              {t.emphasis && (
                <span className="text-caption rounded-full border border-primary bg-primary-subtle px-2 py-0.5 font-medium text-primary">
                  Most teams
                </span>
              )}
            </div>
            <div className="mt-4 flex items-baseline gap-1.5">
              <span className="text-display-3 tnum font-mono text-foreground-strong">
                {t.price}
              </span>
              <span className="text-ui-sm text-muted-foreground">{t.cadence}</span>
            </div>
            <p className="text-ui-sm mt-3 text-muted-foreground">{t.blurb}</p>

            <ul className="mt-6 flex-1 space-y-2.5">
              {t.features.map((f) => (
                <li key={f} className="text-ui-sm flex gap-2.5 text-foreground">
                  <span
                    aria-hidden
                    className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ background: t.emphasis ? "var(--primary)" : "var(--muted-foreground)" }}
                  />
                  {f}
                </li>
              ))}
            </ul>

            <Link
              href={t.href}
              className={`text-ui mt-7 inline-flex h-10 items-center justify-center rounded-md font-medium transition-colors duration-[120ms] ${
                t.emphasis
                  ? "bg-primary text-primary-foreground hover:bg-primary-hover"
                  : "border border-border-strong bg-surface-raised text-foreground hover:bg-surface"
              }`}
            >
              {t.cta}
            </Link>
          </div>
        ))}
      </div>

      <section className="mt-20">
        <h2 className="text-display-3 text-foreground-strong">Questions</h2>
        <div className="mt-6 max-w-[76ch] space-y-3">
          {FAQ.map((f) => (
            <details key={f.q} className="rounded-lg border border-border bg-surface px-5 py-4">
              <summary className="text-title-sm cursor-pointer text-foreground-strong marker:text-muted-foreground">
                {f.q}
              </summary>
              <p className="text-ui-sm mt-3 text-muted-foreground">{f.a}</p>
            </details>
          ))}
        </div>
      </section>
    </div>
  );
}
