"""Provider catalogs (model_catalog.py, tts_catalog.py) and Module 2's
/api/asins/validate endpoint."""
import time
import unittest
from unittest.mock import Mock, patch


class CatalogFallbackTests(unittest.TestCase):
    """No network calls here -- these exercise the built-in static fallback
    path every catalog must have when a provider is unreachable or
    unconfigured, so the UI is never empty."""

    def test_llm_model_catalog_falls_back_without_a_key(self):
        import web_app.model_catalog as model_catalog

        result = model_catalog.list_models("gemini", api_key="")
        self.assertEqual(result["source"], "static")
        self.assertTrue(result["items"])
        self.assertIn(result["defaultModel"], [m["id"] for m in result["items"]])

    def test_tts_voice_catalog_has_static_fallback_for_every_provider(self):
        import web_app.tts_catalog as tts_catalog

        for provider_id in tts_catalog.PROVIDER_IDS:
            fallback = tts_catalog.VOICE_FALLBACKS[provider_id]()
            if provider_id in {"elevenlabs", "cartesia", "ai33pro"}:
                continue  # no catalog until a key is configured; by design
            self.assertTrue(fallback, f"{provider_id} has no static voice fallback")

    def test_unknown_llm_provider_raises_keyerror(self):
        import web_app.model_catalog as model_catalog

        with self.assertRaises(KeyError):
            model_catalog.list_models("not-a-real-provider")


class CatalogCacheTests(unittest.TestCase):
    def test_resolve_uses_cache_before_refetching(self):
        import tempfile
        from pathlib import Path

        import web_app.catalog_cache as catalog_cache

        with tempfile.TemporaryDirectory() as temp:
            real_path = Path(temp) / "cache.json"
            with patch.object(catalog_cache, "CATALOG_CACHE_FILE", real_path):
                calls = {"n": 0}

                def fetcher():
                    calls["n"] += 1
                    return [{"id": "x"}]

                first = catalog_cache.resolve("k", fetcher, [], refresh=False)
                second = catalog_cache.resolve("k", fetcher, [], refresh=False)
                self.assertEqual(first["source"], "live")
                self.assertEqual(second["source"], "cache")
                self.assertEqual(calls["n"], 1)

                third = catalog_cache.resolve("k", fetcher, [], refresh=True)
                self.assertEqual(third["source"], "live")
                self.assertEqual(calls["n"], 2)


class AsinValidateApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import web_app.app as app_module

        cls.module = app_module
        app_module.app.config.update(TESTING=True)

    def setUp(self):
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["is_activated"] = True
            session["last_activation_check"] = time.time()
            session["user_email"] = "test@example.com"
            session["video_quota"] = "unlimited"
            session["video_used"] = 0
            session["csrf_token"] = "test-csrf"

    def test_unconfigured_credentials_fall_back_to_scraping(self):
        # v7: without Amazon Creators API credentials, ASINs are no longer
        # dumped into MANUAL_REVIEW by default -- they're scraped from the
        # public product page instead (asin_lookup.py), same technique the
        # render pipeline already uses. Mock the scraper so this test has no
        # real network dependency.
        fake_row = {
            "asin": "B000W93Q32", "name": "Example Shoe", "imageUrl": "https://example.com/x.jpg",
            "price": "$49.99", "availability": "AVAILABLE",
            "affiliateUrl": "https://www.amazon.com/dp/B000W93Q32", "validationStatus": "SCRAPED",
        }
        with patch.object(self.module, "get_settings", return_value={}), patch.object(
            self.module.asin_lookup, "lookup_asins", return_value={"B000W93Q32": fake_row}
        ) as lookup:
            response = self.client.post(
                "/api/asins/validate",
                json={"rows": [{"keyword": "walking shoes", "asins": ["B000W93Q32", "B09BN1XPQC"]}]},
                headers={"X-CSRF-Token": "test-csrf"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(lookup.called)
        payload = response.get_json()
        self.assertFalse(payload["configured"])
        row = payload["data"][0]
        self.assertEqual(row["keyword"], "walking shoes")
        by_asin = {p["asin"]: p for p in row["products"]}
        self.assertEqual(by_asin["B000W93Q32"]["validationStatus"], "SCRAPED")
        self.assertEqual(by_asin["B000W93Q32"]["name"], "Example Shoe")
        # The ASIN the fake scraper didn't return still degrades gracefully.
        self.assertEqual(by_asin["B09BN1XPQC"]["validationStatus"], "NOT_FOUND")

    def test_rejects_more_than_twenty_rows(self):
        rows = [{"keyword": f"kw{i}", "asins": ["B000W93Q32"]} for i in range(21)]
        response = self.client.post(
            "/api/asins/validate",
            json={"rows": rows},
            headers={"X-CSRF-Token": "test-csrf"},
        )
        self.assertEqual(response.status_code, 422)

    def test_invalid_asin_shapes_are_dropped_before_lookup(self):
        with patch.object(self.module, "get_settings", return_value={}):
            response = self.client.post(
                "/api/asins/validate",
                json={"rows": [{"keyword": "kw", "asins": ["not-an-asin", "B000W93Q32"]}]},
                headers={"X-CSRF-Token": "test-csrf"},
            )
        payload = response.get_json()
        products = payload["data"][0]["products"]
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["asin"], "B000W93Q32")

    def test_requires_csrf(self):
        response = self.client.post(
            "/api/asins/validate", json={"rows": [{"keyword": "kw", "asins": ["B000W93Q32"]}]}
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
