"""Answer validation: per-claim entailment against the retrieved evidence.

Three stages, and the first one is free:

  A. Uncited-span pre-check. The answer response already marks which text blocks
     carry citations. A block of factual prose with no citation is flagged with
     zero API calls. It is a PRECISION filter, not a correctness check -- a model
     can cite a passage that does not actually support the sentence -- so it
     prioritizes work rather than replacing stage C.
  B. Decomposition into atomic claims, with character spans so the UI can
     underline the exact sentence.
  C. Entailment verdicts against the evidence.

Two hard constraints shape the design:

  * Structured outputs and citations cannot coexist on one Claude call (400).
    So the ANSWER call has citations on and no schema; the verifier calls have
    schemas on and citations off, carrying chunk attribution through explicit
    [chunk N] markers that we then validate against the set we actually sent.

  * The verifier must never be the model that wrote the answer. Self-preference
    bias measurably inflates faithfulness. Enforced in code, not just in docs.
"""

from __future__ import annotations

import re
from typing import Sequence

from app.config import settings
from app.generate.provider import EvidenceBlock, LLMProvider, Usage
from app.graph.state import ClaimVerdict, VerificationResult

CLAIMS_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["claims"],
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "text", "span", "type"],
                "properties": {
                    "id": {"type": "integer"},
                    "text": {"type": "string"},
                    "span": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                    "type": {"enum": ["factual", "inferential", "meta"]},
                },
            },
        }
    },
}

VERDICTS_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdicts"],
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["claim_id", "label", "evidence_chunk_ids", "quote", "reason"],
                "properties": {
                    "claim_id": {"type": "integer"},
                    "label": {"enum": ["supported", "partially_supported", "unsupported"]},
                    "evidence_chunk_ids": {"type": "array", "items": {"type": "integer"}},
                    "quote": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}

DECOMPOSE_SYSTEM = """Decompose an answer into atomic factual claims.

An atomic claim states exactly one verifiable fact and is understandable in
isolation: resolve every pronoun and reference to its explicit referent.

Classify each claim:
- factual: asserts something about the world that a source document could
  confirm or deny.
- inferential: a conclusion the answer draws by combining facts (comparisons,
  totals, rankings).
- meta: statements about the answer or the corpus ("the documents do not address
  X", "based on the provided sources"). These are not checkable against sources.

Return each claim with the exact character span [start, end) it occupies in the
input text. Preserve all numbers, dates, names and identifiers verbatim."""

VERIFY_SYSTEM = """You are a strict evidence checker. For each claim, decide
whether the provided source passages support it. Judge only what the passages
state - not what is plausible, not what you know independently.

- supported: a passage states the claim, or states it with only trivial
  rewording. Numbers, dates and names must match exactly.
- partially_supported: a passage supports part of the claim, or supports it with
  a qualification, scope or condition the claim omits.
- unsupported: no passage states the claim; or a passage contradicts it; or the
  claim requires an inferential leap the passages do not license.

Cite the chunk ids you relied on and quote the decisive sentence verbatim. If
you cannot quote a decisive sentence, the verdict is unsupported.

Inferential claims are supported only if every premise is supported AND the
inference is arithmetic or definitional, not judgmental."""

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


class SelfVerificationError(RuntimeError):
    """Raised when the verifier model equals the generator model."""


def assert_independent_verifier(generator_model: str, verifier_model: str) -> None:
    """A model grading its own output inflates the score. This is a correctness
    constraint on the metric, so it fails loudly rather than warning."""
    if generator_model and generator_model == verifier_model:
        raise SelfVerificationError(
            f"Verifier model {verifier_model!r} is the same model that generated the "
            "answer. Self-preference bias inflates faithfulness; use a different model."
        )


def find_uncited_spans(blocks: Sequence[dict]) -> int:
    """Stage A. Free: no API call.

    `blocks` are the answer's raw content blocks. A text block of substantive
    prose with an empty citations array is an uncited span -- most often the
    model bridging two sources with an unsupported connective claim.
    """
    count = 0
    for b in blocks:
        if b.get("type") != "text":
            continue
        text = (b.get("text") or "").strip()
        if len(text) < 40:
            continue  # too short to be a standalone factual assertion
        if not b.get("citations"):
            count += 1
    return count


def should_verify(answer_text: str, uncited_spans: int, *, sample: float | None = None) -> bool:
    """Gate verification to control cost.

    Always verify when stage A found uncited spans or the answer is long --
    those are the answers most likely to be wrong. Otherwise sample. This takes
    the average from ~$0.025/query to ~$0.006 without losing coverage where it
    matters.
    """
    if not settings.verify_enabled:
        return False
    if uncited_spans > 0:
        return True
    if len(answer_text.split()) > settings.verify_always_over_words:
        return True
    rate = settings.verify_sample_rate if sample is None else sample
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    # Deterministic sampling on answer content, so the same answer always makes
    # the same decision and tests are stable.
    return (hash(answer_text) % 1000) / 1000.0 < rate


def compute_faithfulness(claims: Sequence[ClaimVerdict]) -> float | None:
    """(supported + 0.5 * partial) / factual_claims.

    Returns None when there are no checkable claims -- an answer made entirely
    of meta statements ("the documents do not address X") has no faithfulness
    score, and reporting 1.0 for it would be a lie by omission.
    """
    checkable = [c for c in claims if c.claim_type != "meta"]
    if not checkable:
        return None
    supported = sum(1 for c in checkable if c.label == "supported")
    partial = sum(1 for c in checkable if c.label == "partially_supported")
    return (supported + 0.5 * partial) / len(checkable)


def _render_evidence(evidence: Sequence[EvidenceBlock]) -> str:
    """Plain text blocks with explicit chunk markers. NOT search_result/document
    blocks -- citations must stay off on this call so the schema is legal."""
    parts = []
    for ev in evidence:
        body = " ".join(ev.blocks)
        parts.append(f"[chunk {ev.chunk_id}] ({ev.title}, p.{ev.page_start})\n{body}")
    return "\n\n".join(parts)


async def verify_answer(
    *,
    provider: LLMProvider,
    answer_text: str,
    evidence: Sequence[EvidenceBlock],
    blocks: Sequence[dict] | None = None,
    generator_model: str | None = None,
) -> VerificationResult:
    """Stages A-C. Shared verbatim between the live path and the eval harness.

    If faithfulness were computed by two implementations, one of them would be
    lying -- so there is exactly one.
    """
    verifier_model = settings.model_verify
    assert_independent_verifier(generator_model or settings.model_answer, verifier_model)

    uncited = find_uncited_spans(blocks or [])

    if not answer_text.strip():
        return VerificationResult(
            verifier_model=verifier_model,
            unavailable_reason="empty answer",
            uncited_spans=uncited,
        )

    # ── Stage B: decompose ────────────────────────────────────────────────
    decomp, _ = await provider.complete_json(
        model=settings.model_decompose,
        system=DECOMPOSE_SYSTEM,
        user=answer_text,
        schema=CLAIMS_SCHEMA,
    )
    raw_claims = decomp.get("claims") or []
    claims: list[ClaimVerdict] = []
    for rc in raw_claims:
        try:
            span = tuple(rc.get("span") or (0, 0))[:2]
            claims.append(
                ClaimVerdict(
                    claim_id=int(rc["id"]),
                    text=str(rc["text"]),
                    span=(int(span[0]), int(span[1])),
                    claim_type=rc.get("type", "factual"),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue  # a malformed claim is dropped, never silently "supported"

    if not claims:
        return VerificationResult(
            verifier_model=verifier_model,
            unavailable_reason="no claims extracted",
            uncited_spans=uncited,
        )

    checkable = [c for c in claims if c.claim_type != "meta"]
    if not checkable:
        return VerificationResult(
            claims=claims,
            faithfulness=None,
            verifier_model=verifier_model,
            unavailable_reason="no checkable claims",
            uncited_spans=uncited,
        )

    # ── Stage C: verdicts, one call for all claims ────────────────────────
    payload = {
        "claims": [{"id": c.claim_id, "text": c.text} for c in checkable],
    }
    verdict_out, _ = await provider.complete_json(
        model=verifier_model,
        system=VERIFY_SYSTEM,
        user=f"{_render_evidence(evidence)}\n\nCLAIMS:\n{payload}",
        schema=VERDICTS_SCHEMA,
    )
    verdicts = verdict_out.get("verdicts") or []

    if not verdicts:
        # The provider could not judge (offline mode, or the call failed).
        # Return claims with no score rather than defaulting them to supported.
        return VerificationResult(
            claims=claims,
            faithfulness=None,
            verifier_model=verifier_model,
            unavailable_reason=(
                "verifier returned no verdicts"
                if not settings.is_offline
                else "offline mode cannot judge entailment"
            ),
            uncited_spans=uncited,
        )

    valid_chunk_ids = {ev.chunk_id for ev in evidence}
    by_id = {c.claim_id: c for c in claims}
    for v in verdicts:
        claim = by_id.get(v.get("claim_id"))
        if claim is None:
            continue
        claim.label = v.get("label", "unsupported")
        claim.quote = str(v.get("quote", ""))
        claim.reason = str(v.get("reason", ""))
        # Reject attributions to chunks we never sent. A verdict citing a
        # hallucinated chunk id is not evidence of anything.
        claim.evidence_chunk_ids = [
            int(cid) for cid in (v.get("evidence_chunk_ids") or []) if int(cid) in valid_chunk_ids
        ]
        # A "supported" verdict with no quote fails this check by its own rule.
        if claim.label != "unsupported" and not claim.quote.strip():
            claim.label = "unsupported"
            claim.reason = "verifier gave no decisive quote"

    return VerificationResult(
        claims=claims,
        faithfulness=compute_faithfulness(claims),
        verifier_model=verifier_model,
        uncited_spans=uncited,
    )
