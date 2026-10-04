"use client";

import type { AskResponse, RerankedRow } from "@/lib/ask-types";

/**
 * The retrieval inspector.
 *
 * Defaults to a table, not a flow diagram: the table answers "why did this
 * result win" in one glance, and a four-column bump chart of forty candidates
 * is a hairball at any opacity.
 *
 * The leftmost signal after rank is the rerank delta, because one column
 * explaining what the cross-encoder did is worth more than any chart.
 */
export function RetrievalInspector({ data }: { data: AskResponse }) {
  const rows = data.retrieval.reranked;

  return (
    <div>
      <header className="border-b border-border px-4 py-3">
        <p className="text-overline text-muted-foreground">Query understanding</p>
        {data.rewrite?.skipped ? (
          <p className="text-ui-sm mt-1.5 text-muted-foreground">
            Rewriting skipped &mdash; the question was already standalone and
            specific enough to embed directly.
          </p>
        ) : (
          <div className="mt-1.5 space-y-1">
            <p className="text-ui-sm font-mono text-foreground">
              {data.rewrite?.standalone_query}
            </p>
            {data.rewrite?.keywords && data.rewrite.keywords.length > 0 && (
              <div className="flex flex-wrap gap-1.5 pt-1">
                {data.rewrite.keywords.map((k) => (
                  <span
                    key={k}
                    className="text-caption rounded border px-1.5 py-0.5 font-mono"
                    style={{
                      color: "var(--lane-lexical)",
                      borderColor: "var(--lane-lexical)",
                      background: "var(--lane-lexical-fill)",
                    }}
                  >
                    {k}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="text-caption tnum mt-3 flex flex-wrap gap-x-3 gap-y-1 font-mono text-subtle-foreground">
          {Object.entries(data.latency_ms).map(([stage, ms]) => (
            <span key={stage}>
              {stage} {ms.toFixed(0)}ms
            </span>
          ))}
        </div>
      </header>

      <div className="border-b border-border bg-surface-sunken px-4 py-2">
        <div className="text-caption tnum flex flex-wrap gap-x-4 font-mono text-muted-foreground">
          {Object.entries(data.retrieval.lanes).map(([lane, n]) => (
            <span key={lane}>
              lane {lane}: {n}
            </span>
          ))}
          <span>fused: {data.retrieval.fused}</span>
          <span>in context: {rows.filter((r) => r.in_context).length}</span>
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="text-ui-sm px-4 py-6 text-muted-foreground">
          Nothing cleared the relevance floors, so no evidence was sent to the
          model. That is why the answer is a refusal.
        </p>
      ) : (
        <>
          <table className="w-full">
            <thead>
              <tr className="text-overline border-b border-border text-muted-foreground">
                <th className="px-3 py-2 text-left font-semibold">#</th>
                <th className="px-1 py-2 text-left font-semibold" title="How far the cross-encoder moved this result">
                  &Delta;
                </th>
                <th className="px-3 py-2 text-left font-semibold">Source</th>
                <th className="px-3 py-2 text-left font-semibold">Lanes</th>
                <th className="px-3 py-2 text-right font-semibold">Fused</th>
                <th className="px-3 py-2 text-right font-semibold">Rerank</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <Row key={r.chunk_id} row={r} rank={i + 1} />
              ))}
            </tbody>
          </table>
          <p className="text-caption border-t border-border px-4 py-2.5 text-subtle-foreground">
            BM25 and cosine scores are not comparable and are never mixed. Bars
            show each lane&rsquo;s rank for this query only; the fused column is
            the RRF contribution.
          </p>
        </>
      )}
    </div>
  );
}

function Row({ row, rank }: { row: RerankedRow; rank: number }) {
  return (
    <tr
      className="border-b border-border last:border-0"
      style={{
        opacity: row.in_context ? 1 : 0.6,
        borderLeft: row.in_context ? "2px solid var(--primary)" : "2px solid transparent",
      }}
    >
      <td className="text-ui-sm tnum px-3 py-2 font-mono text-muted-foreground">{rank}</td>
      <td className="px-1 py-2">
        <Delta value={row.delta} />
      </td>
      <td className="px-3 py-2">
        <div className="text-ui-sm text-foreground">{row.title}</div>
        <div className="text-caption tnum font-mono text-subtle-foreground">p.{row.page}</div>
      </td>
      <td className="px-3 py-2">
        <div className="flex flex-col gap-1">
          <LaneBadge lane="L" rank={row.bm25_rank} />
          <LaneBadge lane="S" rank={row.vec_rank} />
        </div>
      </td>
      <td className="text-ui-sm tnum px-3 py-2 text-right font-mono text-foreground">
        {row.rrf.toFixed(4)}
      </td>
      <td className="text-ui-sm tnum px-3 py-2 text-right font-mono text-foreground">
        {row.rerank === null ? "—" : row.rerank.toFixed(3)}
      </td>
    </tr>
  );
}

function Delta({ value }: { value: number | null }) {
  if (value === null) return <span className="text-ui-sm text-subtle-foreground">&mdash;</span>;
  const color =
    value > 0 ? "var(--rerank-up)" : value < 0 ? "var(--rerank-down)" : "var(--subtle-foreground)";
  const label = value > 0 ? `↑${value}` : value < 0 ? `↓${Math.abs(value)}` : "—";
  return (
    <span className="text-ui-sm tnum font-mono" style={{ color }} title={`moved ${value} places`}>
      {label}
    </span>
  );
}

/** A dash means this lane never retrieved the chunk. That is distinct from a
 *  zero score and gets its own treatment, because it is the most informative
 *  cell in the table. */
function LaneBadge({ lane, rank }: { lane: "L" | "S"; rank: number | null }) {
  const lexical = lane === "L";
  const color = lexical ? "var(--lane-lexical)" : "var(--lane-semantic)";
  const bg = lexical ? "var(--lane-lexical-fill)" : "var(--lane-semantic-fill)";
  const absent = rank === null;
  return (
    <span
      className="text-caption tnum inline-flex w-[52px] items-center gap-1 rounded border px-1.5 py-0.5 font-mono"
      style={{
        color: absent ? "var(--subtle-foreground)" : color,
        borderColor: absent ? "var(--border)" : color,
        background: absent ? "transparent" : bg,
      }}
      title={absent ? `${lexical ? "Keyword" : "Vector"} lane did not retrieve this` : undefined}
    >
      <span className="font-semibold">{lane}</span>
      <span>{absent ? "—" : `#${rank}`}</span>
    </span>
  );
}
