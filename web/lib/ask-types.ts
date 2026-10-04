/** Shape returned by /api/ask. Mirrors the FastAPI response. */

export interface Citation {
  marker: number;
  evidence_index: number;
  document_id: string;
  title: string;
  page: number;
  cited_text: string;
}

export interface RerankedRow {
  chunk_id: number;
  title: string;
  page: number;
  /** null means this lane never retrieved the chunk - the most informative
   *  state in the table, so it must survive to the UI rather than render as 0. */
  bm25_rank: number | null;
  vec_rank: number | null;
  rrf: number;
  rerank: number | null;
  delta: number | null;
  in_context: boolean;
}

export interface ClaimVerdict {
  claim_id: number;
  text: string;
  span: [number, number];
  claim_type: "factual" | "inferential" | "meta";
  label: "supported" | "partially_supported" | "unsupported";
  evidence_chunk_ids: number[];
  quote: string;
  reason: string;
}

export interface Verification {
  claims: ClaimVerdict[];
  faithfulness: number | null;
  verifier_model: string;
  /** Set when verification could not run. The UI must show this rather than a
   *  score, because a fabricated number is worse than an absent one. */
  unavailable_reason: string | null;
  uncited_spans: number;
}

export interface AskResponse {
  answer: string;
  refused: boolean;
  citations: Citation[];
  retrieval: {
    lanes: Record<string, number>;
    fused: number;
    reranked: RerankedRow[];
  };
  rewrite: {
    standalone_query: string;
    subqueries: string[];
    hyde: string;
    keywords: string[];
    intent: string;
    skipped: boolean;
  } | null;
  verification: Verification | null;
  latency_ms: Record<string, number>;
  errors: string[];
}
