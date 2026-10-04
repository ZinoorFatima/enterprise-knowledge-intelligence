import { Fragment } from "react";
import { ThemeToggle } from "@/components/theme-toggle";

/**
 * Internal design-system reference. Not linked from anywhere public.
 *
 * This page exists to be judged BEFORE forty components depend on the tokens.
 * If the system looks wrong here, it is cheap to fix; after the marketing site
 * and the app shell are built on it, it is not.
 */
export const metadata = { title: "Design system", robots: { index: false } };

const SURFACES = [
  ["--background", "bg-background"],
  ["--surface", "bg-surface"],
  ["--surface-sunken", "bg-surface-sunken"],
  ["--surface-raised", "bg-surface-raised"],
] as const;

const TEXT = [
  ["--foreground-strong", "text-foreground-strong", "15.9:1 - headings"],
  ["--foreground", "text-foreground", "15.9:1 - body"],
  ["--muted-foreground", "text-muted-foreground", "5.2:1 - secondary"],
  ["--subtle-foreground", "text-subtle-foreground", "3.4:1 - decorative only"],
] as const;

const SEMANTIC = [
  ["--primary", "bg-primary"],
  ["--success", "bg-success"],
  ["--warning", "bg-warning"],
  ["--danger", "bg-danger"],
  ["--info", "bg-info"],
] as const;

const TYPE = [
  ["text-display-1", "Answers your auditors can check", "60/62/-0.032em/600"],
  ["text-display-2", "Ten stages, all inspectable", "44/48/-0.028em/600"],
  ["text-display-3", "Where it fails", "32/38/-0.022em/600"],
  ["text-title-lg", "Document library", "24/30/-0.016em/600"],
  ["text-title", "Retrieval inspector", "18/26/-0.011em/600"],
  ["text-title-sm", "Verification", "15/22/-0.006em/600"],
  ["text-body-lg", "Marketing body copy sits at seventeen pixels.", "17/28"],
  ["text-body", "Answer prose in the app sits at fifteen.", "15/24"],
  ["text-ui", "App default: buttons, tables, labels.", "14/20"],
  ["text-ui-sm", "Dense tables and inspector rows.", "13/18"],
  ["text-caption", "Badges, axis labels, timestamps", "12/16/500"],
  ["text-overline", "Retrieval-augmented generation", "11/14/0.08em/600"],
] as const;

function Section({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-t border-border py-10">
      <h2 className="text-title-sm text-foreground-strong">{title}</h2>
      {note && (
        <p className="text-ui-sm mt-1 max-w-[68ch] text-muted-foreground">{note}</p>
      )}
      <div className="mt-5">{children}</div>
    </section>
  );
}

function Swatch({ token, cls }: { token: string; cls: string }) {
  return (
    <div>
      <div className={`h-14 rounded-md border border-border ${cls}`} />
      <div className="text-caption tnum mt-1.5 text-muted-foreground">{token}</div>
    </div>
  );
}

export default function DesignPage() {
  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-[1120px] px-6 py-12">
        <header className="flex items-start justify-between gap-6">
          <div>
            <p className="text-overline text-muted-foreground">Internal</p>
            <h1 className="text-display-3 mt-1 text-foreground-strong">
              Design system
            </h1>
            <p className="text-body mt-2 max-w-[68ch] text-muted-foreground">
              Every token, both themes. Toggle and check that nothing depends on a
              color defined in only one of them.
            </p>
          </div>
          <ThemeToggle />
        </header>

        <Section
          title="Surfaces"
          note="Elevation in the app comes from surface steps and a 1px border, never a shadow. Shadows are reserved for things that actually float."
        >
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {SURFACES.map(([token, cls]) => (
              <Swatch key={token} token={token} cls={cls} />
            ))}
          </div>
        </Section>

        <Section title="Text" note="Contrast ratios measured against --background.">
          <div className="space-y-2.5">
            {TEXT.map(([token, cls, note]) => (
              <div key={token} className="flex flex-wrap items-baseline gap-x-4">
                <span className={`text-body ${cls}`}>
                  The quick brown fox jumps over the lazy dog
                </span>
                <span className="text-caption tnum text-subtle-foreground">
                  {token} · {note}
                </span>
              </div>
            ))}
          </div>
        </Section>

        <Section title="Semantic">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
            {SEMANTIC.map(([token, cls]) => (
              <Swatch key={token} token={token} cls={cls} />
            ))}
          </div>
        </Section>

        <Section
          title="Retrieval lanes"
          note="Exactly two hues. Amber and violet is the one pair that stays separable under deuteranopia, protanopia and tritanopia simultaneously. Fused and reranked are deliberately neutral — color here would imply a quality judgement the score already makes."
        >
          <div className="flex flex-wrap gap-3">
            <LaneBadge lane="L" rank={3} fill={4} />
            <LaneBadge lane="S" rank={12} fill={2} />
            <LaneBadge lane="L" rank={null} fill={0} />
            <LaneBadge lane="S" rank={1} fill={5} />
          </div>
          <p className="text-ui-sm mt-4 max-w-[68ch] text-muted-foreground">
            A dash means the lane never retrieved that chunk — the most
            informative state in the table, so it gets its own visual treatment
            rather than a zero-length bar. Lane color is never the only encoding:
            every badge also carries an L/S letter and a numeric rank.
          </p>
        </Section>

        <Section
          title="Rerank delta"
          note="The single column that explains what the cross-encoder did better than any chart."
        >
          <div className="flex gap-6">
            <Delta value={7} />
            <Delta value={0} />
            <Delta value={-1} />
            <Delta value={-13} />
          </div>
        </Section>

        <Section
          title="Verification verdicts"
          note="Every verdict carries a word, not just a colored dot."
        >
          <div className="flex flex-wrap gap-2">
            <Verdict label="Supported" tone="supported" />
            <Verdict label="Partially supported" tone="partial" />
            <Verdict label="Unsupported" tone="unsupported" />
          </div>
          <p className="text-body mt-5 max-w-[72ch] text-foreground">
            The liability cap is twelve months of fees
            <Chip n={1} />, except for claims arising from IP infringement
            <Chip n={2} />.{" "}
            <span className="decoration-warning [text-decoration-line:underline] [text-decoration-style:wavy] [text-underline-offset:3px]">
              The cap resets annually on the renewal date.
            </span>
          </p>
          <p className="text-ui-sm mt-2 text-muted-foreground">
            An unsupported sentence gets a wavy underline in the answer itself —
            not a footnote the reader has to go looking for.
          </p>
        </Section>

        <Section title="Type scale">
          <div className="space-y-4">
            {TYPE.map(([cls, sample, spec]) => (
              <div key={cls} className="flex flex-wrap items-baseline gap-x-4">
                <span className={`${cls} text-foreground-strong`}>{sample}</span>
                <span className="text-caption tnum text-subtle-foreground">
                  {cls} · {spec}
                </span>
              </div>
            ))}
          </div>
        </Section>

        <Section
          title="Tabular numerals"
          note="JetBrains Mono, tabular, for every number that gets compared to another number. This one rule is what makes the inspector legible."
        >
          <div className="grid max-w-md grid-cols-[auto_auto_1fr] gap-x-8 gap-y-1">
            <span className="text-overline text-muted-foreground">Fused</span>
            <span className="text-overline text-muted-foreground">Rerank</span>
            <span />
            {[
              ["0.031", "0.883"],
              ["0.024", "0.851"],
              ["0.119", "0.402"],
            ].map(([fused, rerank]) => (
              <Fragment key={fused}>
                <span className="text-ui-sm tnum font-mono text-foreground">{fused}</span>
                <span className="text-ui-sm tnum font-mono text-foreground">{rerank}</span>
                <span />
              </Fragment>
            ))}
          </div>
        </Section>

        <Section title="Radius and elevation">
          <div className="flex flex-wrap items-end gap-4">
            {(["sm", "md", "lg", "xl", "2xl"] as const).map((r) => (
              <div key={r} className="text-center">
                <div
                  className="h-16 w-16 border border-border bg-surface-raised"
                  style={{ borderRadius: `var(--radius-${r})` }}
                />
                <div className="text-caption mt-1.5 text-muted-foreground">{r}</div>
              </div>
            ))}
            {(["xs", "sm", "md", "lg"] as const).map((s) => (
              <div key={s} className="text-center">
                <div
                  className="h-16 w-16 rounded-lg bg-surface-raised"
                  style={{ boxShadow: `var(--shadow-${s})` }}
                />
                <div className="text-caption mt-1.5 text-muted-foreground">
                  shadow-{s}
                </div>
              </div>
            ))}
          </div>
        </Section>

        <Section title="Controls">
          <div className="flex flex-wrap items-center gap-3">
            <button className="text-ui inline-flex h-9 items-center rounded-md bg-primary px-3.5 font-medium text-primary-foreground transition-colors duration-[120ms] hover:bg-primary-hover">
              Start free
            </button>
            <button className="text-ui inline-flex h-9 items-center rounded-md border border-border-strong bg-surface-raised px-3.5 font-medium text-foreground transition-colors duration-[120ms] hover:border-border-strong hover:bg-surface">
              See the evaluation
            </button>
            <button className="text-ui inline-flex h-9 items-center rounded-md px-3 font-medium text-muted-foreground transition-colors duration-[120ms] hover:bg-surface hover:text-foreground">
              Ghost
            </button>
            <input
              placeholder="Filter documents"
              className="text-ui h-9 rounded-md border border-border-strong bg-background px-3 text-foreground placeholder:text-subtle-foreground"
            />
          </div>
        </Section>
      </div>
    </div>
  );
}

function LaneBadge({
  lane,
  rank,
  fill,
}: {
  lane: "L" | "S";
  rank: number | null;
  fill: number;
}) {
  const lexical = lane === "L";
  const color = lexical ? "var(--lane-lexical)" : "var(--lane-semantic)";
  const bg = lexical ? "var(--lane-lexical-fill)" : "var(--lane-semantic-fill)";
  return (
    <div
      className="inline-flex items-center gap-2 rounded-md border px-2 py-1"
      style={{ borderColor: color, background: bg }}
    >
      <span className="text-caption font-mono font-semibold" style={{ color }}>
        {lane}
      </span>
      <span className="text-caption tnum font-mono" style={{ color }}>
        {rank === null ? "—" : `#${rank}`}
      </span>
      <span className="flex gap-[2px]" aria-hidden>
        {[0, 1, 2, 3, 4].map((i) => (
          <span
            key={i}
            className="h-3 w-[3px] rounded-[1px]"
            style={{
              background: i < fill ? color : "transparent",
              border: i < fill ? "none" : `1px solid ${color}`,
              opacity: i < fill ? 1 : 0.3,
            }}
          />
        ))}
      </span>
    </div>
  );
}

function Delta({ value }: { value: number }) {
  const tone =
    value > 0
      ? "var(--rerank-up)"
      : value < 0
        ? "var(--rerank-down)"
        : "var(--subtle-foreground)";
  return (
    <span className="text-ui-sm tnum font-mono" style={{ color: tone }}>
      {value > 0 ? `↑${value}` : value < 0 ? `↓${Math.abs(value)}` : "—"}
    </span>
  );
}

function Verdict({
  label,
  tone,
}: {
  label: string;
  tone: "supported" | "partial" | "unsupported";
}) {
  const map = {
    supported: ["var(--success)", "var(--success-subtle)"],
    partial: ["var(--warning)", "var(--warning-subtle)"],
    unsupported: ["var(--danger)", "var(--danger-subtle)"],
  } as const;
  const [fg, bg] = map[tone];
  return (
    <span
      className="text-caption inline-flex items-center rounded-full border px-2.5 py-1 font-medium"
      style={{ color: fg, background: bg, borderColor: fg }}
    >
      {label}
    </span>
  );
}

function Chip({ n }: { n: number }) {
  return (
    <button
      type="button"
      className="mx-[2px] inline-flex h-[18px] -translate-y-px items-center rounded-[5px] border border-primary-border bg-primary-subtle px-[5px] align-baseline font-mono text-[11px] leading-none text-primary transition-colors duration-[120ms] hover:bg-primary hover:text-primary-foreground"
      aria-label={`Source ${n}. Open in source viewer.`}
    >
      {n}
    </button>
  );
}
