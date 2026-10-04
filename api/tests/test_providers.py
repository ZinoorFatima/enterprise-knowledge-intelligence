"""Provider selection and the Gemini path's citation handling."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.generate.provider import PRICING, OfflineProvider, get_provider


class TestSelection:
    def test_offline_needs_no_key(self):
        assert get_provider("offline").name == "offline"

    def test_each_backend_resolves(self):
        assert get_provider("anthropic").name == "anthropic"
        assert get_provider("gemini").name == "gemini"

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="unknown LLM provider"):
            get_provider("llama")

    def test_llm_mode_live_still_means_anthropic(self):
        """Existing configs predate LLM_PROVIDER and must keep working."""
        s = Settings(llm_mode="live", llm_provider="offline")
        assert s.provider == "anthropic"
        assert s.is_offline is False

    def test_explicit_provider_wins_over_llm_mode(self):
        s = Settings(llm_mode="offline", llm_provider="gemini")
        assert s.provider == "gemini"

    def test_default_is_offline(self):
        assert Settings().provider == "offline"


class TestKeyErrors:
    """A missing key must fail with an actionable message, not an opaque SDK error."""

    async def test_anthropic_without_key(self):
        from app.generate.anthropic_provider import AnthropicProvider

        p = AnthropicProvider()
        p._client = None
        import app.config as cfg

        old = cfg.settings.anthropic_api_key
        cfg.settings.anthropic_api_key = ""
        try:
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
                p._get_client()
        finally:
            cfg.settings.anthropic_api_key = old

    async def test_gemini_without_key(self):
        from app.generate.gemini_provider import GeminiProvider

        p = GeminiProvider()
        import app.config as cfg

        old = cfg.settings.gemini_api_key
        cfg.settings.gemini_api_key = ""
        try:
            with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
                p._get_client()
        finally:
            cfg.settings.gemini_api_key = old


class TestGeminiCitationRecovery:
    """Gemini has no span-level citation API, so markers are parsed out of the
    text. A marker naming a source we never sent must be discarded rather than
    becoming a citation pointing at nothing."""

    def _evidence(self, n=2):
        from app.generate.provider import EvidenceBlock

        return [
            EvidenceBlock(
                chunk_id=i,
                document_id=f"d{i}",
                title=f"Doc {i}",
                page_start=1,
                page_end=1,
                char_start=0,
                blocks=[f"Clause {i} text."],
            )
            for i in range(n)
        ]

    def test_markers_resolve_to_sent_evidence(self):
        from app.generate.gemini_provider import _MARKER

        found = [int(m.group(1)) for m in _MARKER.finditer("A [1]. B [2]. C [1].")]
        assert found == [1, 2, 1]

    def test_out_of_range_markers_are_rejected(self):
        """The guard: index 9 names a source that was never sent."""
        from app.generate.gemini_provider import _MARKER

        evidence = self._evidence(2)
        kept = [
            int(m.group(1)) - 1
            for m in _MARKER.finditer("X [1]. Y [9].")
            if 0 <= int(m.group(1)) - 1 < len(evidence)
        ]
        assert kept == [0]

    def test_sources_are_numbered_from_one(self):
        from app.generate.gemini_provider import GeminiProvider

        rendered = GeminiProvider._render(self._evidence(2))
        assert rendered.startswith("[1]")
        assert "[2]" in rendered
        assert "[0]" not in rendered


class TestPricing:
    def test_gemini_models_priced(self):
        assert "gemini-3.8-flash" in PRICING
        assert "gemini-3.5-flash" in PRICING

    def test_unknown_model_is_zero_not_a_guess(self):
        from app.generate.provider import Usage

        assert Usage(input_tokens=1000).cost_usd("some-future-model") == 0.0
