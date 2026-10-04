"""Gemini provider.

The only place in this codebase that constructs a Google GenAI client.

An important asymmetry to be honest about: Gemini has no equivalent of
Anthropic's document-citation feature, which returns the exact span the model
drew on. Here, citations are recovered by asking the model to tag sentences
with the evidence index it used, then matching those tags back to the blocks we
sent -- and any tag naming a block we did not send is discarded.

That is strictly weaker than span-level citations returned by the API, and a
comparison between the two providers has to say so rather than presenting the
citation columns as like-for-like.
"""

from __future__ import annotations

import json
import re
from typing import AsyncIterator, Sequence

from app.config import settings
from app.generate.provider import Citation, EvidenceBlock, StreamEvent, Usage

ANSWER_SYSTEM = """You answer questions about an enterprise document corpus using
only the numbered sources provided.

Rules:
- Ground every factual statement in the provided sources. Do not use outside
  knowledge.
- If the sources do not contain the answer, say so plainly and name what is
  missing. A correct refusal is a good answer; a plausible guess is a failure.
- After each sentence that uses a source, append its marker in square brackets,
  for example [2]. Use only the numbers shown. Never invent a number.
- Do not restate the question. Do not add a preamble. Lead with the answer.
- Quantities, dates, names and identifiers must be copied exactly."""

_MARKER = re.compile(r"\[(\d+)\]")


class GeminiProvider:
    """Implements LLMProvider against the Google GenAI API."""

    name = "gemini"

    def __init__(self, model: str | None = None):
        self.model = model or settings.gemini_model
        self._client = None

    @property
    def judge_model(self) -> str:
        """The model this provider uses to grade answers.

        Must differ from the generation model: a model grading its own output
        inflates faithfulness. Same-vendor judging reduces that bias but does
        not remove it, and the eval report labels runs judged this way.
        """
        return settings.gemini_judge_model

    def _get_client(self):
        if self._client is None:
            from google import genai

            if not settings.gemini_api_key:
                raise RuntimeError(
                    "GEMINI_API_KEY is not set. Add it to .env, or set "
                    "LLM_PROVIDER=offline to run retrieval without a key."
                )
            self._client = genai.Client(api_key=settings.gemini_api_key)
        return self._client

    @staticmethod
    def _render(evidence: Sequence[EvidenceBlock]) -> str:
        """Numbered sources. The number is the index into `evidence`, which is
        what a returned marker is resolved against."""
        parts = []
        for i, ev in enumerate(evidence):
            body = "\n".join(f"  ({j}) {b}" for j, b in enumerate(ev.blocks))
            parts.append(f"[{i + 1}] {ev.title} (p.{ev.page_start})\n{body}")
        return "\n\n".join(parts)

    @staticmethod
    def _usage(resp) -> Usage:
        m = getattr(resp, "usage_metadata", None)
        if m is None:
            return Usage()
        return Usage(
            input_tokens=getattr(m, "prompt_token_count", 0) or 0,
            output_tokens=getattr(m, "candidates_token_count", 0) or 0,
            cache_read_input_tokens=getattr(m, "cached_content_token_count", 0) or 0,
        )

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

        from google.genai import types

        client = self._get_client()
        prompt = f"{self._render(evidence)}\n\nQUESTION: {question}"

        try:
            text_parts: list[str] = []
            last = None
            stream = await client.aio.models.generate_content_stream(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=ANSWER_SYSTEM,
                    max_output_tokens=settings.answer_max_tokens,
                    temperature=0.0,
                ),
            )
            async for chunk in stream:
                last = chunk
                piece = getattr(chunk, "text", None)
                if piece:
                    text_parts.append(piece)
                    yield StreamEvent("text", text=piece)

            # Recover citations from the markers the model emitted. Anything
            # naming a source we did not send is dropped rather than trusted.
            full = "".join(text_parts)
            seen: set[int] = set()
            for m in _MARKER.finditer(full):
                idx = int(m.group(1)) - 1
                if idx in seen or not (0 <= idx < len(evidence)):
                    continue
                seen.add(idx)
                ev = evidence[idx]
                if not ev.blocks:
                    continue
                yield StreamEvent(
                    "citation",
                    citation=Citation(
                        evidence_index=idx,
                        start_block_index=0,
                        end_block_index=1,
                        # Block-level, not span-level: Gemini does not return the
                        # exact range it drew on, so the whole block is cited.
                        cited_text=ev.blocks[0],
                    ),
                )

            yield StreamEvent("usage", usage=self._usage(last))
            yield StreamEvent("done")
        except Exception as exc:  # noqa: BLE001
            yield StreamEvent("error", error=str(exc)[:300])

    async def complete_json(
        self, *, model: str, system: str, user: str, schema: dict, max_tokens: int = 4000
    ) -> tuple[dict, Usage]:
        from google.genai import types

        client = self._get_client()
        resp = await client.aio.models.generate_content(
            # The CALLER's model, not self.model. Substituting the generator
            # here would make the verifier grade its own output while the
            # independence check still reported a pass.
            model=model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
                temperature=0.0,
                response_mime_type="application/json",
                response_json_schema=schema,
            ),
        )
        try:
            data = json.loads(resp.text or "{}")
        except (json.JSONDecodeError, TypeError):
            # The caller treats {} as "could not judge" rather than as an empty
            # but valid answer, so a malformed payload never becomes a verdict.
            data = {}
        return data, self._usage(resp)
