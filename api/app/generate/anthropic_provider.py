"""Claude provider.

The only place in this codebase that constructs an Anthropic client.

Two API constraints shape this file and are easy to get wrong:

  * `output_config.format` (structured outputs) and `citations` cannot coexist
    on one request -- it returns 400. So the ANSWER call has citations on and no
    schema, and the JSON calls (claim decomposition, entailment verdicts, query
    rewriting) have a schema and no citations.

  * Thinking is configured as `{"type": "adaptive"}`. `budget_tokens` is removed
    on current models and returns 400; depth is controlled with
    `output_config.effort` instead.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Sequence

from app.config import settings
from app.generate.provider import Citation, EvidenceBlock, StreamEvent, Usage

ANSWER_SYSTEM = """You answer questions about an enterprise document corpus using
only the search results provided in the user turn.

Rules:
- Ground every factual statement in the provided documents. Do not use outside
  knowledge.
- If the documents do not contain the answer, say so plainly and name what is
  missing. A correct refusal is a good answer; a plausible guess is a failure.
- When sources disagree, present both readings and attribute each.
- Do not restate the question. Do not add a preamble. Lead with the answer.
- Quantities, dates, names and identifiers must be copied exactly."""


class AnthropicProvider:
    """Implements LLMProvider against the Anthropic Messages API."""

    name = "anthropic"

    def __init__(self, model: str | None = None):
        self.model = model or settings.model_answer
        self._client = None

    @property
    def judge_model(self) -> str:
        return settings.model_verify

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic

            if not settings.anthropic_api_key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set. Add it to .env, or set "
                    "LLM_PROVIDER=offline to run retrieval without a key."
                )
            self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        return self._client

    @staticmethod
    def _documents(evidence: Sequence[EvidenceBlock]) -> list[dict]:
        """Evidence as custom-content document blocks.

        Each chunk becomes one document whose content is a list of text blocks,
        so a returned citation carries `start_block_index`/`end_block_index`
        into a list we constructed. Resolution is therefore index arithmetic
        against our own structures -- no string matching, and no class of bug
        where a citation fails to resolve or lands on the wrong passage.
        """
        out = []
        for ev in evidence:
            title = f"{ev.title} - p.{ev.page_start}"
            if ev.page_end != ev.page_start:
                title += f"-{ev.page_end}"
            out.append(
                {
                    "type": "document",
                    "source": {
                        "type": "content",
                        "content": [{"type": "text", "text": b} for b in ev.blocks],
                    },
                    "title": title,
                    "citations": {"enabled": True},
                }
            )
        return out

    async def stream_answer(
        self,
        question: str,
        evidence: Sequence[EvidenceBlock],
        history: Sequence[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if not evidence:
            msg = (
                "I could not find support for that question in the indexed documents. "
                "Nothing retrieved scored above the relevance threshold, so there is "
                "no evidence to cite."
            )
            yield StreamEvent("text", text=msg)
            yield StreamEvent("usage", usage=Usage())
            yield StreamEvent("done")
            return

        client = self._get_client()
        try:
            async with client.messages.stream(
                model=self.model,
                max_tokens=settings.answer_max_tokens,
                # NEVER budget_tokens: removed on current models, returns 400.
                thinking={"type": "adaptive"},
                # NOT output_config.format -- that would 400 alongside citations.
                output_config={"effort": settings.answer_effort},
                system=[
                    {
                        "type": "text",
                        "text": ANSWER_SYSTEM,
                        # The ONLY cache breakpoint in a single-shot query. The
                        # evidence below is unique per request, so a breakpoint
                        # after it would pay the 1.25x write premium on tokens
                        # that are never read back.
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[
                    *(history or []),
                    {
                        "role": "user",
                        "content": [
                            *self._documents(evidence),
                            {"type": "text", "text": question},
                        ],
                    },
                ],
            ) as stream:
                async for event in stream:
                    if event.type != "content_block_delta":
                        continue
                    delta = event.delta
                    if getattr(delta, "type", None) == "text_delta":
                        yield StreamEvent("text", text=delta.text)
                    elif getattr(delta, "type", None) == "citations_delta":
                        c = delta.citation
                        yield StreamEvent(
                            "citation",
                            citation=Citation(
                                evidence_index=getattr(c, "document_index", 0) or 0,
                                start_block_index=getattr(c, "start_block_index", 0) or 0,
                                end_block_index=getattr(c, "end_block_index", 1) or 1,
                                cited_text=getattr(c, "cited_text", "") or "",
                            ),
                        )
                final = await stream.get_final_message()

            u = final.usage
            yield StreamEvent(
                "usage",
                usage=Usage(
                    input_tokens=getattr(u, "input_tokens", 0) or 0,
                    output_tokens=getattr(u, "output_tokens", 0) or 0,
                    cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                    cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
                ),
            )
            yield StreamEvent("done")
        except Exception as exc:  # noqa: BLE001
            yield StreamEvent("error", error=str(exc)[:300])

    async def complete_json(
        self, *, model: str, system: str, user: str, schema: dict, max_tokens: int = 4000
    ) -> tuple[dict, Usage]:
        """Structured output. Citations stay OFF here -- the two are mutually
        exclusive on one request."""
        client = self._get_client()
        resp = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # A malformed payload must not be mistaken for an empty-but-valid
            # result; the caller treats {} as "could not judge".
            data = {}
        u = resp.usage
        return data, Usage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
