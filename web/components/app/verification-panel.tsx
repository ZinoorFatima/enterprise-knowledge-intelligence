"use client";

import type { ClaimVerdict, Verification } from "@/lib/ask-types";

const TONE = {
  supported: ["var(--claim-supported)", "var(--success-subtle)", "Supported"],
  partially_supported: ["var(--claim-partial)", "var(--warning-subtle)", "Partially supported"],
  unsupported: ["var(--claim-unsupported)", "var(--danger-subtle)", "Unsupported"],
} as const;

export function VerificationPanel({
  verification,
  onHoverClaim,
}: {
  verification: Verification | null;
  onHoverClaim?: (span: [number, number] | null) => void;
}) {
  if (!verification) {
    return (
      <p className="text-ui-sm px-4 py-6 text-muted-foreground">
        No verification was attached to this answer.
      </p>
    );
  }

  // An answer that was not checked must say so. On a product whose claim is
  // "you can check this", silently omitting verification is the worst available
  // failure, and a fabricated score is worse than an absent one.
  if (verification.unavailable_reason) {
    return (
      <div className="px-4 py-5">
        <div className="rounded-md border border-warning bg-warning-subtle px-3.5 py-3">
          <p className="text-ui-sm font-medium text-warning">This answer was not verified</p>
          <p className="text-ui-sm mt-1 text-warning">{verification.unavailable_reason}</p>
        </div>
        {verification.claims.length > 0 && (
          <>
            <p className="text-ui-sm mt-5 text-muted-foreground">
              Claims were still extracted, so you can see what would be checked:
            </p>
            <ul className="mt-3 space-y-2">
              {verification.claims.map((c) => (
                <li
                  key={c.claim_id}
                  className="text-ui-sm rounded-md border border-border bg-surface px-3 py-2 text-foreground"
                  onMouseEnter={() => onHoverClaim?.(c.span)}
                  onMouseLeave={() => onHoverClaim?.(null)}
                >
                  {c.text}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    );
  }

  const factual = verification.claims.filter((c) => c.claim_type !== "meta");
  const counts = {
    supported: factual.filter((c) => c.label === "supported").length,
    partially_supported: factual.filter((c) => c.label === "partially_supported").length,
    unsupported: factual.filter((c) => c.label === "unsupported").length,
  };
  // Unsupported first: the thing most worth a reader's attention sorts to the top.
  const ordered = [...factual].sort((a, b) => weight(a) - weight(b));

  return (
    <div className="px-4 py-5">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-title-sm text-foreground-strong">
          {counts.supported} of {factual.length} claims supported
        </p>
        {verification.faithfulness !== null && (
          <span className="text-ui-sm tnum font-mono text-foreground">
            {verification.faithfulness.toFixed(3)}
          </span>
        )}
      </div>

      <div className="mt-2.5 flex h-1.5 overflow-hidden rounded-full bg-surface-sunken">
        {(["supported", "partially_supported", "unsupported"] as const).map((k) =>
          counts[k] > 0 ? (
            <div
              key={k}
              style={{
                width: `${(counts[k] / Math.max(factual.length, 1)) * 100}%`,
                background: TONE[k][0],
              }}
            />
          ) : null,
        )}
      </div>

      <p className="text-caption mt-2 text-subtle-foreground">
        Measures groundedness in the retrieved passages, not truth: a faithful
        summary of a wrong source still scores 1.0. Checked by{" "}
        <span className="font-mono">{verification.verifier_model}</span>, which is
        deliberately not the model that wrote the answer.
      </p>

      <ul className="mt-4 space-y-2">
        {ordered.map((c) => (
          <ClaimCard key={c.claim_id} claim={c} onHover={onHoverClaim} />
        ))}
      </ul>
    </div>
  );
}

function weight(c: ClaimVerdict): number {
  return c.label === "unsupported" ? 0 : c.label === "partially_supported" ? 1 : 2;
}

function ClaimCard({
  claim,
  onHover,
}: {
  claim: ClaimVerdict;
  onHover?: (span: [number, number] | null) => void;
}) {
  const [fg, bg, label] = TONE[claim.label];
  return (
    <li
      className="rounded-md border border-border bg-surface-raised p-3"
      onMouseEnter={() => onHover?.(claim.span)}
      onMouseLeave={() => onHover?.(null)}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="text-ui-sm text-foreground">{claim.text}</p>
        <span
          className="text-caption shrink-0 rounded-full border px-2 py-0.5 font-medium"
          style={{ color: fg, background: bg, borderColor: fg }}
        >
          {label}
        </span>
      </div>
      {claim.quote && (
        <blockquote
          className="text-ui-sm mt-2 border-l-2 pl-2.5 text-muted-foreground"
          style={{ borderColor: fg }}
        >
          &ldquo;{claim.quote}&rdquo;
        </blockquote>
      )}
      {claim.label === "unsupported" && claim.reason && (
        <p className="text-caption mt-2 text-muted-foreground">{claim.reason}</p>
      )}
    </li>
  );
}
