"""metadata_generator._build_llm_chain() -- this module keeps its own copy of
the chain-building logic (separate process from amazon_video_maker.py), and
it used to lack "vertex_gemini" entirely: _LLM_PROVIDERS didn't list it and
there was no auto-fallback loop, so a user who configured Vertex AI as their
LLM_SERVICE got silently downgraded to whatever provider happened to be first
in the tuple (longcat) for every metadata call, even with no longcat key
saved. Mirrors test_llm_fallback_chain.py."""
import unittest
from unittest.mock import patch

from app_files import metadata_generator as mg


class MetadataLlmFallbackChainTests(unittest.TestCase):
    def setUp(self):
        patches = {
            "LLM_SERVICE": "vertex_gemini",
            "GEMINI_API_KEYS": [],
            "OPENROUTER_API_KEYS": [],
            "OPENAI_API_KEYS": [],
            "DEEPSEEK_API_KEYS": [],
            "LONGCAT_API_KEYS": [],
            "LLM_FALLBACK_ENABLED": False,
            "LLM_CHAIN_RAW": "",
            "VERTEX_PROJECT_ID": "",
            "VERTEX_SERVICE_ACCOUNT_JSON": "",
        }
        self._patchers = [patch.object(mg, name, value) for name, value in patches.items()]
        for p in self._patchers:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patchers])

    def test_vertex_gemini_is_a_recognized_primary_provider(self):
        # Previously "vertex_gemini" wasn't in _LLM_PROVIDERS, so this silently
        # became "longcat" instead of the user's actual choice.
        chain = mg._build_llm_chain()
        self.assertEqual(chain[0]["provider"], "vertex_gemini")

    def test_does_not_fall_back_to_longcat_specifically_when_unconfigured(self):
        # The bug report: only longcat was ever tried regardless of selection.
        # With nothing configured anywhere, longcat must not be silently
        # substituted as the primary.
        chain = mg._build_llm_chain()
        self.assertNotEqual(chain[0]["provider"], "longcat")

    def test_falls_back_to_any_other_provider_with_a_saved_key_automatically(self):
        mg.OPENAI_API_KEYS.append("sk-openai-key")
        chain = mg._build_llm_chain()
        providers = [entry["provider"] for entry in chain]
        self.assertEqual(providers[0], "vertex_gemini")
        self.assertIn("openai", providers)

    def test_manual_fallback_chain_is_tried_before_the_automatic_one(self):
        mg.LLM_FALLBACK_ENABLED = True
        mg.LLM_CHAIN_RAW = "deepseek|deepseek-chat"
        mg.DEEPSEEK_API_KEYS.append("deepseek-key")
        mg.OPENAI_API_KEYS.append("sk-openai-key")
        chain = mg._build_llm_chain()
        providers = [entry["provider"] for entry in chain]
        self.assertEqual(providers, ["vertex_gemini", "deepseek", "openai"])


if __name__ == "__main__":
    unittest.main()
