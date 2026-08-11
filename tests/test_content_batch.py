import ipaddress
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from web_app.content_batch import (
    BatchStore,
    ContentBatchManager,
    CreatorsApiClient,
    extract_article,
    fetch_article_html,
    normalize_source_urls,
    validate_public_url,
)


ARTICLE_HTML = """
<!doctype html>
<html>
  <head>
    <title>7 Best Robot Vacuums for Pet Hair (2026)</title>
    <meta property="og:title" content="Best Robot Vacuums for Pet Hair">
  </head>
  <body>
    <h2>Best Overall: CleanBot X1</h2>
    <p>A strong option for homes with pets.</p>
    <a href="https://www.amazon.com/dp/B0ABC12345?tag=example-20">Check price</a>
    <h2>Best Budget: DustMate Mini</h2>
    <a href="https://amazon.com/gp/product/B0XYZ98765">View on Amazon</a>
    <a href="https://www.amazon.com/dp/B0ABC12345">Duplicate product link</a>
  </body>
</html>
"""


class SourceUrlTests(unittest.TestCase):
    def test_normalizes_and_deduplicates_up_to_twenty_urls(self):
        values = [
            "https://Example.com/reviews/?utm_source=newsletter#top",
            "https://example.com/reviews",
            "https://another.example/best-products",
        ]

        self.assertEqual(
            normalize_source_urls(values),
            [
                "https://example.com/reviews",
                "https://another.example/best-products",
            ],
        )

    def test_rejects_more_than_twenty_urls(self):
        with self.assertRaisesRegex(ValueError, "20"):
            normalize_source_urls(
                [f"https://example{i}.com/article" for i in range(21)]
            )

    def test_rejects_private_dns_results(self):
        def private_resolver(_host, _port, type=0):
            return [(2, type, 6, "", ("127.0.0.1", 443))]

        with self.assertRaisesRegex(ValueError, "public"):
            validate_public_url(
                "https://reviews.example/article", resolver=private_resolver
            )

    def test_accepts_public_dns_results(self):
        def public_resolver(_host, _port, type=0):
            address = str(ipaddress.ip_address("93.184.216.34"))
            return [(2, type, 6, "", (address, 443))]

        self.assertEqual(
            validate_public_url(
                "https://reviews.example/article", resolver=public_resolver
            ),
            "https://reviews.example/article",
        )


class ArticleExtractionTests(unittest.TestCase):
    def test_extracts_unique_ranked_amazon_products(self):
        result = extract_article(
            "https://reviews.example/robot-vacuums", ARTICLE_HTML
        )

        self.assertEqual(result["contentType"], "ROUNDUP")
        # The URL slug ("robot-vacuums") is the keyword source now, not the
        # page's <title> -- it's what the source site optimized for SEO.
        self.assertEqual(result["keyword"], "Robot Vacuums")
        self.assertEqual(
            [product["asin"] for product in result["products"]],
            ["B0ABC12345", "B0XYZ98765"],
        )
        self.assertEqual(result["products"][0]["name"], "CleanBot X1")
        self.assertGreaterEqual(result["confidence"], 80)

    def test_strips_site_name_suffix_from_title_and_keyword(self):
        # v6 used the raw <title> tag verbatim, including the site's own
        # branding suffix (e.g. "Best Dash Cam Review - Car Mechan").
        html = """
        <html><head>
            <title>Best Dash Cam Review: Top Rated - Car Mechan</title>
            <meta property="og:site_name" content="Car Mechan">
        </head><body>
            <h2>Best Overall: Nexar Pro</h2>
            <a href="https://www.amazon.com/dp/B0ABC12345">Check price</a>
        </body></html>
        """
        result = extract_article("https://carmechan.com/dash-cam-review/", html)
        self.assertNotIn("Car Mechan", result["articleTitle"])
        self.assertNotIn("Car Mechan", result["keyword"])
        self.assertTrue(result["articleTitle"].startswith("Best Dash Cam Review"))

    def test_falls_back_to_hostname_when_no_site_name_meta(self):
        html = """
        <html><head><title>Best Dash Cam Review - carmechan</title></head>
        <body><a href="https://www.amazon.com/dp/B0ABC12345">Check price</a></body></html>
        """
        result = extract_article("https://carmechan.com/x/", html)
        self.assertEqual(result["articleTitle"], "Best Dash Cam Review")

    def test_humanizes_url_slug_when_page_has_no_title_at_all(self):
        # Regression: a page with no <title>/og:title fell back to the raw
        # URL path segment verbatim -- "best-nexar-pro-dash-cam-review-top%E2%80%91rated-2026"
        # rendered on screen with literal hyphens and a tofu box for the
        # %E2%80%91 (non-breaking hyphen) the video font can't display.
        html = """
        <html><head></head>
        <body><a href="https://www.amazon.com/dp/B0ABC12345">Check price</a></body></html>
        """
        result = extract_article(
            "https://example.com/reviews/best-nexar-pro-dash-cam-review-top%E2%80%91rated-2026",
            html,
        )
        self.assertEqual(
            result["articleTitle"],
            "Best Nexar Pro Dash Cam Review Top Rated 2026",
        )

    def test_resolves_cloaked_affiliate_redirect_to_amazon(self):
        # Most content sites (news orgs, buying-guide blogs) route outbound
        # product links through their own tracking/cloaking redirector
        # rather than linking amazon.com directly. Without resolving those,
        # "any website" only ever worked for sites happening to link Amazon
        # raw -- a small minority.
        html = """
        <html><body>
            <h2>Best Overall: Nexar Pro Dash Cam</h2>
            <a href="https://go.example-redirector.com/click?id=abc">Check Price</a>
        </body></html>
        """
        with patch(
            "web_app.content_batch._resolve_redirect_target",
            return_value="https://www.amazon.com/dp/B0ABC12345",
        ):
            result = extract_article("https://cnn.example/reviews/best-dash-cam", html)
        self.assertEqual(len(result["products"]), 1)
        self.assertEqual(result["products"][0]["asin"], "B0ABC12345")

    def test_direct_amazon_links_take_priority_over_resolved_redirects(self):
        html = """
        <html><body>
            <h2>Pick 1</h2>
            <a href="https://go.example-redirector.com/click?id=abc">Check Price</a>
            <h2>Pick 2</h2>
            <a href="https://www.amazon.com/dp/B0DIRECT99">Check Price</a>
        </body></html>
        """
        with patch(
            "web_app.content_batch._resolve_redirect_target",
            return_value="https://www.amazon.com/dp/B0RESOLVE1",
        ):
            result = extract_article("https://reviews.example/picks", html)
        asins = [p["asin"] for p in result["products"]]
        # The direct link (pass 1, no network round-trip) must be resolved
        # before -- and therefore ranked ahead of -- the redirector one.
        self.assertEqual(asins[0], "B0DIRECT99")
        self.assertIn("B0RESOLVE1", asins)

    def test_non_amazon_redirect_targets_are_discarded(self):
        html = """
        <html><body>
            <h2>Pick</h2>
            <a href="https://go.example-redirector.com/click?id=abc">Check Price</a>
        </body></html>
        """
        with patch(
            "web_app.content_batch._resolve_redirect_target",
            return_value="https://www.bestbuy.com/site/some-product",
        ):
            result = extract_article("https://reviews.example/picks", html)
        self.assertEqual(result["products"], [])


class RemoteFetchTests(unittest.TestCase):
    def test_default_validator_preserves_trailing_slash_across_redirect_hops(self):
        # Root-caused against a real site: a WordPress-style host 301s the
        # no-trailing-slash form of a URL to the trailing-slash form as its
        # canonical URL. validate_public_url() used to route through
        # _canonical_source_url(), which strips trailing slashes -- so each
        # hop got its slash stripped right back off, the server 301'd it
        # back on, and the loop bounced between the two forms forever
        # ("Source redirected too many times") even though the page was
        # fetchable on the very first real request.
        def public_resolver(_host, _port, type=0):
            address = str(ipaddress.ip_address("93.184.216.34"))
            return [(2, type, 6, "", (address, 443))]

        no_slash = "https://reviews.example/buying-guide/widget-review"
        with_slash = no_slash + "/"
        redirect = Mock(status_code=301, headers={"Location": with_slash})
        redirect.close = Mock()
        final = Mock(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            encoding="utf-8",
        )
        final.iter_content.return_value = [b"<html>ok</html>"]
        final.close = Mock()
        session = Mock()
        session.get.side_effect = [redirect, final]

        html = fetch_article_html(
            no_slash,
            session=session,
            validator=lambda url: validate_public_url(url, resolver=public_resolver),
        )

        self.assertEqual(html, "<html>ok</html>")
        self.assertEqual(session.get.call_count, 2)
        # The second request must actually go to the WITH-slash URL the
        # server redirected to, not get bounced back to the bare form.
        self.assertEqual(session.get.call_args_list[1].args[0], with_slash)

    def test_revalidates_redirect_and_limits_response_size(self):
        redirect = Mock(
            status_code=302,
            headers={"Location": "https://other.example/article"},
        )
        redirect.close = Mock()
        final = Mock(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            encoding="utf-8",
        )
        final.iter_content.return_value = [b"<html>ok</html>"]
        final.close = Mock()
        session = Mock()
        session.get.side_effect = [redirect, final]
        validated = []

        html = fetch_article_html(
            "https://reviews.example/article",
            session=session,
            validator=lambda url: validated.append(url) or url,
        )

        self.assertEqual(html, "<html>ok</html>")
        self.assertEqual(
            validated,
            [
                "https://reviews.example/article",
                "https://other.example/article",
            ],
        )
        self.assertEqual(session.get.call_count, 2)

    def test_rejects_non_html_response(self):
        response = Mock(
            status_code=200,
            headers={"Content-Type": "application/pdf"},
            encoding="utf-8",
        )
        response.close = Mock()
        session = Mock()
        session.get.return_value = response

        with self.assertRaisesRegex(ValueError, "HTML"):
            fetch_article_html(
                "https://reviews.example/file",
                session=session,
                validator=lambda url: url,
            )


class CreatorsApiTests(unittest.TestCase):
    def test_enriches_products_with_verified_amazon_data(self):
        requester = Mock()
        token_response = Mock(status_code=200)
        token_response.json.return_value = {
            "access_token": "access-token",
            "expires_in": 3600,
        }
        token_response.raise_for_status = Mock()
        items_response = Mock(status_code=200)
        items_response.json.return_value = {
            "itemsResult": {
                "items": [
                    {
                        "asin": "B0ABC12345",
                        "detailPageURL": "https://www.amazon.com/dp/B0ABC12345?tag=test-20",
                        "itemInfo": {
                            "title": {"displayValue": "Verified CleanBot X1"},
                            "features": {
                                "displayValues": ["Self-emptying", "LiDAR navigation"]
                            },
                        },
                        "offersV2": {
                            "listings": [
                                {
                                    "availability": {
                                        "type": "IN_STOCK",
                                        "message": "In Stock",
                                    }
                                }
                            ]
                        },
                    }
                ]
            }
        }
        items_response.raise_for_status = Mock()
        requester.post.side_effect = [token_response, items_response]
        client = CreatorsApiClient(
            {
                "creators_api_client_id": "client",
                "creators_api_client_secret": "secret",
                "creators_api_credential_version": "3.1",
                "partner_tag": "test-20",
            },
            requester=requester,
        )

        enriched = client.enrich_products(
            [{"asin": "B0ABC12345", "name": "Article Name", "isIncluded": True}]
        )

        self.assertEqual(enriched[0]["name"], "Verified CleanBot X1")
        self.assertEqual(enriched[0]["availability"], "IN_STOCK")
        self.assertEqual(enriched[0]["validationStatus"], "VERIFIED")
        self.assertIn("tag=test-20", enriched[0]["affiliateUrl"])


class BatchStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "content-jobs.sqlite3"
        self.store = BatchStore(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_persists_review_edit_and_approved_generator_lines(self):
        batch = self.store.create_batch(
            [
                "https://reviews.example/robot-vacuums",
                "https://reviews.example/coffee-makers",
            ]
        )
        first, second = batch["jobs"]
        self.store.complete_job(
            first["jobId"],
            {
                "articleTitle": "Best Robot Vacuums",
                "keyword": "Best Robot Vacuums",
                "contentType": "ROUNDUP",
                "confidence": 92,
                "revenuePotential": "HIGH",
                "products": [
                    {"asin": "B0ABC12345", "name": "CleanBot X1", "isIncluded": True},
                    {"asin": "B0XYZ98765", "name": "DustMate", "isIncluded": False},
                ],
            },
        )
        self.store.complete_job(
            second["jobId"],
            {
                "articleTitle": "Best Coffee Makers",
                "keyword": "Best Coffee Makers",
                "contentType": "ROUNDUP",
                "confidence": 88,
                "revenuePotential": "MEDIUM",
                "products": [
                    {"asin": "B0COFFEE01", "name": "Brew One", "isIncluded": True}
                ],
            },
        )

        updated = self.store.update_job(
            first["jobId"],
            {
                "keyword": "Robot Vacuums for Pet Hair",
                "isApproved": True,
                "products": [
                    {"asin": "B0ABC12345", "name": "CleanBot X1", "isIncluded": True},
                    {"asin": "B0XYZ98765", "name": "DustMate", "isIncluded": True},
                ],
            },
        )

        self.assertTrue(updated["isApproved"])
        self.assertEqual(
            self.store.approved_generator_lines(batch["batchId"]),
            ["Robot Vacuums for Pet Hair, B0ABC12345, B0XYZ98765"],
        )

    def test_only_ready_jobs_can_be_approved(self):
        batch = self.store.create_batch(["https://reviews.example/article"])
        job = batch["jobs"][0]

        with self.assertRaisesRegex(ValueError, "READY"):
            self.store.update_job(job["jobId"], {"isApproved": True})

    def test_marks_products_repeated_across_batch_jobs(self):
        batch = self.store.create_batch(
            [
                "https://reviews.example/first",
                "https://reviews.example/second",
            ]
        )
        for job in batch["jobs"]:
            self.store.complete_job(
                job["jobId"],
                {
                    "keyword": "Buyer Guide",
                    "contentType": "SINGLE",
                    "confidence": 90,
                    "revenuePotential": "HIGH",
                    "products": [
                        {
                            "asin": "B0ABC12345",
                            "name": "Repeated Product",
                            "isIncluded": True,
                        }
                    ],
                },
            )

        refreshed = self.store.get_batch(batch["batchId"])

        self.assertTrue(refreshed["jobs"][0]["products"][0]["duplicateAcrossBatch"])
        self.assertEqual(
            refreshed["jobs"][0]["products"][0]["batchOccurrenceCount"],
            2,
        )


class ContentBatchManagerTests(unittest.TestCase):
    def test_reuses_creators_client_until_credentials_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = BatchStore(Path(temp_dir) / "jobs.sqlite3")
            settings = {
                "creators_api_client_id": "client",
                "creators_api_client_secret": "secret",
                "creators_api_credential_version": "3.1",
                "partner_tag": "example-20",
            }
            manager = ContentBatchManager(store, lambda: dict(settings))
            try:
                first = manager._creators_client()
                second = manager._creators_client()
                settings["partner_tag"] = "other-20"
                third = manager._creators_client()
            finally:
                manager.executor.shutdown(wait=True)

        self.assertIs(first, second)
        self.assertIsNot(first, third)


if __name__ == "__main__":
    unittest.main()
