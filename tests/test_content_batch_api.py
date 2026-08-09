import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from web_app.content_batch import BatchStore


class ContentBatchApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import web_app.app as app_module

        cls.module = app_module
        app_module.app.config.update(TESTING=True)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = BatchStore(Path(self.temp_dir.name) / "jobs.sqlite3")
        self.manager = Mock()
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session["is_activated"] = True
            session["last_activation_check"] = time.time()
            session["user_email"] = "test@example.com"
            session["video_quota"] = "unlimited"
            session["video_used"] = 0
            session["csrf_token"] = "test-csrf"
        self.store_patch = patch.object(
            self.module, "CONTENT_BATCH_STORE", self.store, create=True
        )
        self.manager_patch = patch.object(
            self.module, "CONTENT_BATCH_MANAGER", self.manager, create=True
        )
        self.store_patch.start()
        self.manager_patch.start()

    def tearDown(self):
        self.manager_patch.stop()
        self.store_patch.stop()
        self.temp_dir.cleanup()

    @property
    def headers(self):
        return {"X-CSRF-Token": "test-csrf"}

    def test_creates_batch_and_dispatches_parallel_analysis(self):
        response = self.client.post(
            "/api/content-batches",
            json={
                "urls": [
                    "https://reviews.example/best-vacuums",
                    "https://guide.example/coffee-makers",
                ]
            },
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 202)
        payload = response.get_json()["data"]
        self.assertEqual(len(payload["jobs"]), 2)
        self.manager.start_batch.assert_called_once_with(payload["batchId"])

    def test_patch_approves_ready_review_job(self):
        batch = self.store.create_batch(["https://reviews.example/best-vacuums"])
        job = batch["jobs"][0]
        self.store.complete_job(
            job["jobId"],
            {
                "articleTitle": "Best Vacuums",
                "keyword": "Best Vacuums",
                "contentType": "ROUNDUP",
                "confidence": 90,
                "revenuePotential": "HIGH",
                "products": [
                    {"asin": "B0ABC12345", "name": "Vacuum", "isIncluded": True}
                ],
            },
        )

        response = self.client.patch(
            f"/api/content-jobs/{job['jobId']}",
            json={"keyword": "Pet Hair Vacuums", "isApproved": True},
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        updated = response.get_json()["data"]
        self.assertTrue(updated["isApproved"])
        self.assertEqual(updated["keyword"], "Pet Hair Vacuums")

    def test_prepare_writes_only_approved_rows(self):
        batch = self.store.create_batch(["https://reviews.example/best-vacuums"])
        job = batch["jobs"][0]
        self.store.complete_job(
            job["jobId"],
            {
                "articleTitle": "Best Vacuums",
                "keyword": "Best Vacuums",
                "contentType": "ROUNDUP",
                "confidence": 90,
                "revenuePotential": "HIGH",
                "products": [
                    {"asin": "B0ABC12345", "name": "Vacuum", "isIncluded": True}
                ],
            },
        )
        self.store.update_job(job["jobId"], {"isApproved": True})
        queue_path = Path(self.temp_dir.name) / "keyword-asin.txt"

        with patch.object(self.module, "KEYWORDS_FILE", queue_path):
            response = self.client.post(
                f"/api/content-batches/{batch['batchId']}/prepare",
                json={},
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            queue_path.read_text(encoding="utf-8"),
            "Best Vacuums, B0ABC12345\n",
        )

    def test_batch_routes_require_csrf(self):
        response = self.client.post(
            "/api/content-batches",
            json={"urls": ["https://reviews.example/best-vacuums"]},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
