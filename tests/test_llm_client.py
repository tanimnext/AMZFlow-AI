"""llm_client.call_with_keys/call_chain -- multi-key rotation within a
provider, and cross-provider fallback. Written to give a definitive answer
to "if I paste multiple Gemini API keys, does it actually rotate to the
next one when the first fails?" -- yes, verified here."""
import unittest
from unittest.mock import Mock, patch

from app_files import llm_client


def _resp(status, body):
    response = Mock()
    response.status_code = status
    response.json.return_value = body
    response.text = str(body)
    return response


class MultiKeyRotationTests(unittest.TestCase):
    @patch("app_files.llm_client.requests.post")
    def test_a_bad_first_key_moves_immediately_to_the_second_key(self, post):
        post.side_effect = [
            _resp(401, {"error": "invalid API key"}),  # key 1: fatal, no retry on same key
            _resp(200, {"candidates": [{"content": {"parts": [{"text": "Second key worked."}]}}]}),
        ]
        text = llm_client.call_with_keys(
            "gemini", "prompt", ["bad-key-1", "good-key-2"], "gemini-2.5-flash"
        )
        self.assertEqual(text, "Second key worked.")
        self.assertEqual(post.call_count, 2)
        # Second call must have used the SECOND key, not retried the first.
        self.assertIn("bad-key-1", post.call_args_list[0].args[0])
        self.assertIn("good-key-2", post.call_args_list[1].args[0])

    @patch("app_files.llm_client.requests.post")
    def test_a_rate_limited_key_retries_once_before_moving_to_the_next_key(self, post):
        post.side_effect = [
            _resp(429, {"error": "rate limited"}),  # key 1, attempt 1: retryable
            _resp(429, {"error": "rate limited"}),  # key 1, attempt 2: still rate limited
            _resp(200, {"candidates": [{"content": {"parts": [{"text": "Third call worked."}]}}]}),
        ]
        with patch("app_files.llm_client.time.sleep"):  # skip the real backoff delay
            text = llm_client.call_with_keys(
                "gemini", "prompt", ["rate-limited-key", "good-key"], "gemini-2.5-flash"
            )
        self.assertEqual(text, "Third call worked.")
        self.assertEqual(post.call_count, 3)

    @patch("app_files.llm_client.requests.post")
    def test_every_key_failing_raises_instead_of_silently_returning_empty(self, post):
        post.return_value = _resp(401, {"error": "invalid API key"})
        with self.assertRaises(llm_client.LLMCallError):
            llm_client.call_with_keys("gemini", "prompt", ["bad-1", "bad-2", "bad-3"], "gemini-2.5-flash")
        self.assertEqual(post.call_count, 3)  # each fatal, no per-key retry

    def test_no_keys_configured_fails_fast_without_a_network_call(self):
        with self.assertRaisesRegex(llm_client.LLMCallError, "No API keys configured"):
            llm_client.call_with_keys("gemini", "prompt", [], "gemini-2.5-flash")


class ChainFallbackTests(unittest.TestCase):
    @patch("app_files.llm_client.requests.post")
    def test_every_key_in_the_primary_provider_failing_moves_to_the_next_chain_entry(self, post):
        post.side_effect = [
            _resp(401, {"error": "bad key"}),  # gemini key 1
            _resp(401, {"error": "bad key"}),  # gemini key 2
            _resp(200, {"choices": [{"message": {"content": "OpenAI saved it."}}]}),  # openai fallback
        ]
        chain = [
            {"provider": "gemini", "model": "gemini-2.5-flash", "api_keys": ["g1", "g2"]},
            {"provider": "openai", "model": "gpt-4o-mini", "api_keys": ["sk-openai"]},
        ]
        text, provider_used = llm_client.call_chain("prompt", chain)
        self.assertEqual(text, "OpenAI saved it.")
        self.assertEqual(provider_used, "openai")

    def test_a_chain_entry_with_no_keys_is_skipped_without_a_network_call(self):
        chain = [
            {"provider": "vertex_gemini", "model": "gemini-2.5-flash", "api_keys": [], "endpoint": None},
            {"provider": "gemini", "model": "gemini-2.5-flash", "api_keys": ["g1"]},
        ]
        with patch("app_files.llm_client.requests.post") as post:
            post.return_value = _resp(200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
            text, provider_used = llm_client.call_chain("prompt", chain)
        self.assertEqual(provider_used, "gemini")
        self.assertEqual(post.call_count, 1)  # never attempted the empty vertex_gemini entry


if __name__ == "__main__":
    unittest.main()
