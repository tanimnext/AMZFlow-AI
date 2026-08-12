import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app_files"))

import vertex_auth  # noqa: E402


class VertexAuthTests(unittest.TestCase):
    def test_missing_service_account_json_raises(self):
        with self.assertRaises(ValueError) as ctx:
            vertex_auth.get_access_token("")
        self.assertIn("service account", str(ctx.exception).lower())

    def test_invalid_service_account_json_raises(self):
        with self.assertRaises(ValueError):
            vertex_auth.get_access_token("not json")

    def test_generate_content_url_uses_regional_host(self):
        url = vertex_auth.generate_content_url("my-project", "us-central1", "gemini-2.0-flash-001")
        self.assertEqual(
            url,
            "https://us-central1-aiplatform.googleapis.com/v1/projects/my-project"
            "/locations/us-central1/publishers/google/models/gemini-2.0-flash-001:generateContent",
        )

    def test_generate_content_url_global_location_has_no_region_prefix(self):
        url = vertex_auth.generate_content_url("my-project", "global", "gemini-2.0-flash-001")
        self.assertTrue(url.startswith("https://aiplatform.googleapis.com/"))

    def test_generate_content_url_requires_project_id(self):
        with self.assertRaises(ValueError):
            vertex_auth.generate_content_url("", "us-central1", "gemini-2.0-flash-001")


class VertexSettingsSecurityTests(unittest.TestCase):
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

    def test_vertex_service_account_json_never_reaches_the_browser(self):
        with patch.object(
            self.module,
            "get_settings",
            return_value={"vertex_service_account_private_key": '{"type": "service_account"}'},
        ):
            response = self.client.get("/get_settings")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("vertex_service_account_private_key", response.get_json())

    def test_save_settings_accepts_vertex_ai_fields(self):
        with patch.object(self.module, "save_settings") as save_settings:
            response = self.client.post(
                "/save_settings",
                json={
                    "llm_service": "vertex_gemini",
                    "vertex_project_id": "my-project",
                    "vertex_location": "us-central1",
                    "vertex_llm_model": "gemini-2.0-flash-001",
                    "vertex_tts_model": "gemini-2.5-flash-preview-tts",
                    "vertex_service_account_private_key": '{"type": "service_account"}',
                },
                headers={"X-CSRF-Token": "test-csrf"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True})
        save_settings.assert_called_once()

    def test_saving_other_settings_does_not_erase_an_existing_service_account_key(self):
        # collectSettings() sweeps every input on the page each save, so an
        # empty vertex_service_account_private_key (panel not open, or field
        # genuinely blank) must not silently wipe a previously stored key --
        # same "empty means keep existing" rule every other secret gets.
        with patch.object(
            self.module,
            "get_settings",
            return_value={"vertex_service_account_private_key": '{"type": "service_account"}'},
        ), patch.object(self.module, "save_settings") as save_settings:
            response = self.client.post(
                "/save_settings",
                json={"vertex_service_account_private_key": "", "logo_text": "New Channel Name"},
                headers={"X-CSRF-Token": "test-csrf"},
            )
        self.assertEqual(response.status_code, 200)
        saved_data = save_settings.call_args[0][0]
        self.assertNotIn("vertex_service_account_private_key", saved_data)
        self.assertEqual(saved_data.get("logo_text"), "New Channel Name")


if __name__ == "__main__":
    unittest.main()
