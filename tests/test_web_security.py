import time
import base64
import tempfile
import unittest
from unittest.mock import Mock, patch


class WebSecurityTests(unittest.TestCase):
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

    def test_mutating_route_requires_csrf(self):
        response = self.client.post("/delete_folder", json={"keyword": "anything"})
        self.assertEqual(response.status_code, 403)

    def test_generation_stream_requires_csrf(self):
        response = self.client.get("/run_process")
        self.assertEqual(response.status_code, 403)

    def test_create_pages_define_csrf_token_for_generation_stream(self):
        # v7 reads the CSRF token from the <meta> tag in static/js/core.js
        # (shared by every page) instead of each template inlining its own
        # `window.CSRF_TOKEN = "...".
        with self.client.get("/static/js/core.js") as core_resp:
            self.assertIn(b'window.CSRF_TOKEN = CSRF', core_resp.data)
        for path in ("/create/url", "/create/keywords"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'<meta name="csrf-token" content="test-csrf">', response.data)
        with self.client.get("/static/js/creation.js") as creation_resp:
            self.assertIn(b"/run_process?csrf_token=", creation_resp.data)

    def test_creation_js_maps_long_running_pipeline_progress_messages(self):
        # v7 centralizes the SSE log-line -> progress-bar mapping in
        # static/js/creation.js (shared by both creation modules) instead of
        # duplicating it inline per page.
        response = self.client.get("/static/js/creation.js")
        self.assertEqual(response.status_code, 200)
        for marker in [
            b"Loading Kokoro TTS model",
            b"AUDIO",
            b"Rewriting content",
            b"Downloading Product Media",
            b"Product Assets Ready",
        ]:
            self.assertIn(marker, response.data)

    def test_create_pages_contain_generation_time_settings(self):
        for path in ("/create/url", "/create/keywords"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            for marker in [
                b'id="tts_service"',
                b'id="logo_text"',
                b'id="partner_tag"',
                b'id="shorts_mode"',
                b'id="channel_url"',
            ]:
                self.assertIn(marker, response.data)

    def test_voice_select_is_excluded_from_settings_collection(self):
        # Regression test: #voice_select is a UI-only proxy (the real setting
        # lives at a per-provider id like edge_voice/kokoro_voice, re-pointed
        # onto a hidden input by creation.js). If it's ever missing
        # data-setting="false" again, the browser's generic input-sweep
        # sends a literal "voice_select" key, which /save_settings rejects
        # with "Unknown setting(s)" -- silently breaking Start Generation for
        # every user on both creation pages.
        for path in ("/create/url", "/create/keywords"):
            response = self.client.get(path)
            self.assertIn(b'id="voice_select" class="flex-1" data-setting="false"', response.data)
            self.assertIn(b'data-role="voice-hidden-field"', response.data)

    def test_create_url_page_contains_batch_url_review_workflow(self):
        response = self.client.get("/create/url")
        self.assertEqual(response.status_code, 200)
        for marker in [
            b'id="content_urls"',
            b'id="analyzeContentBtn"',
            b'id="contentReviewTable"',
            b'id="contentReviewBody"',
        ]:
            self.assertIn(marker, response.data)
        with self.client.get("/static/js/creation.js") as creation_resp:
            self.assertIn(b"generateApprovedBatch", creation_resp.data)

    def test_create_keywords_page_contains_asin_validation_workflow(self):
        response = self.client.get("/create/keywords")
        self.assertEqual(response.status_code, 200)
        for marker in [
            b'id="keywords_asin"',
            b'id="validateAsinsBtn"',
            b'id="asinValidationResults"',
        ]:
            self.assertIn(marker, response.data)

    def test_delete_rejects_path_traversal(self):
        response = self.client.post(
            "/delete_folder",
            json={"keyword": "../"},
            headers={"X-CSRF-Token": "test-csrf"},
        )
        self.assertEqual(response.status_code, 400)

    def test_settings_response_never_contains_credentials(self):
        response = self.client.get("/get_settings")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        sensitive = [
            key
            for key in payload
            if any(
                marker in key.lower()
                for marker in ("api_key", "token", "secret", "client_id")
            )
        ]
        self.assertEqual(sensitive, [])

    def test_save_settings_accepts_llm_fallback_and_gemini_tts_fields(self):
        with patch.object(self.module, "save_settings") as save_settings:
            response = self.client.post(
                "/save_settings",
                json={
                    "gemini_tts_voice": "Kore",
                    "llm_fallback_enabled": True,
                    "llm_chain": "gemini|gemini-3.5-flash-lite",
                },
                headers={"X-CSRF-Token": "test-csrf"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True})
        save_settings.assert_called_once()

    def test_save_settings_accepts_editor_run_settings(self):
        with patch.object(self.module, "save_settings") as save_settings:
            response = self.client.post(
                "/save_settings",
                json={
                    "tts_service": "kokoro",
                    "kokoro_voice": "am_michael",
                    "edge_voice": "en-US-AndrewMultilingualNeural",
                    "partner_tag": "example-20",
                    "logo_text": "Test Channel",
                    "channel_url": "https://youtube.com/@test",
                    "shorts_mode": True,
                },
                headers={"X-CSRF-Token": "test-csrf"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True})
        save_settings.assert_called_once()

    def test_save_settings_accepts_content_and_gemini_quality_fields(self):
        with patch.object(self.module, "save_settings") as save_settings:
            response = self.client.post(
                "/save_settings",
                json={
                    "creators_api_client_id": "client-id",
                    "creators_api_client_secret": "client-secret",
                    "creators_api_credential_version": "3.1",
                    "gemini_tts_model": "gemini-3.1-flash-tts-preview",
                    "gemini_tts_voice": "Sadaltager",
                    "gemini_voice_style": "TRUSTED_EXPERT",
                    "gemini_voice_pace": "50",
                    "gemini_voice_energy": "45",
                    "gemini_voice_warmth": "60",
                    "gemini_voice_accent": "US_NEUTRAL",
                    "gemini_voice_instruction": "Pause before the verdict.",
                    "gemini_pronunciations": "LiDAR=lie-dar",
                },
                headers={"X-CSRF-Token": "test-csrf"},
            )
        self.assertEqual(response.status_code, 200)
        save_settings.assert_called_once()

    def test_video_route_rejects_path_traversal(self):
        response = self.client.get("/video/../../etc/passwd")
        self.assertIn(response.status_code, (400, 404))

    def test_video_route_404s_when_no_video_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(self.module, "library_root", return_value=tmp):
                response = self.client.get("/video/nonexistent-project")
        self.assertEqual(response.status_code, 404)

    def test_content_batches_history_reports_missing_video_as_not_found(self):
        fake_job = {
            "jobId": "job1", "keyword": "Best Robot Vacuums",
            "sourceUrl": "https://reviews.example/robot-vacuums",
            "generatedAt": "2026-08-12T00:00:00",
        }
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(self.module, "library_root", return_value=tmp):
            store, _ = self.module._content_batch_services()
            with patch.object(store, "list_generated_jobs", return_value=[fake_job]):
                response = self.client.get("/api/content-batches/history")
        self.assertEqual(response.status_code, 200)
        row = response.get_json()["data"][0]
        self.assertEqual(row["keyword"], "Best Robot Vacuums")
        self.assertFalse(row["hasVideo"])
        self.assertEqual(row["projectId"], "best-robot-vacuums")

    def test_gemini_preview_uses_selected_model_and_director_prompt(self):
        # v7's /preview_tts is async (job id + poll) and cached, and both the
        # preview route and the render pipeline now share tts_engine.py's
        # synth_gemini instead of each carrying its own HTTP call -- so the
        # ffmpeg subprocess it shells out to lives in tts_engine, not app.
        import pathlib

        # app.py imports these as bare `tts_engine` / `preview_service` (its
        # BASE_DIR is prepended to sys.path), which caches them in
        # sys.modules under those bare names. `import web_app.tts_engine`
        # would resolve to a *second*, distinct module object -- patching
        # that copy would silently miss the real call path. Reach the actual
        # instances through the already-imported app module instead.
        preview_service_module = self.module.preview_service
        tts_engine_module = self.module.tts_engine

        api_response = Mock(status_code=200)
        api_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "data": base64.b64encode(b"\0\0").decode("ascii")
                                }
                            }
                        ]
                    }
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            preview_service_module, "PREVIEW_CACHE_DIR", pathlib.Path(temp_dir)
        ), patch.object(
            self.module, "get_settings", return_value={"gemini_api_key": "key"}
        ), patch.object(
            tts_engine_module.requests, "post", return_value=api_response
        ) as post, patch.object(
            tts_engine_module, "_run_ffmpeg"
        ) as run_ffmpeg, patch.object(
            tts_engine_module.os.path, "getsize", return_value=4096
        ), patch.object(
            tts_engine_module.os.path, "exists", return_value=True
        ):
            response = self.client.post(
                "/preview_tts",
                json={
                    "service": "gemini",
                    "text": "The LiDAR sensor maps the room.",
                    "gemini_tts_model": "gemini-2.5-pro-preview-tts",
                    "gemini_tts_voice": "Sulafat",
                    "gemini_voice_style": "PREMIUM_REVIEW",
                    "gemini_pronunciations": "LiDAR=lie-dar",
                },
                headers={"X-CSRF-Token": "test-csrf"},
            )
            self.assertEqual(response.status_code, 200)
            job = response.get_json()

            # The synthesis itself runs on a background thread; give it a
            # moment to reach the (mocked) network call and the ffmpeg step
            # that follows it, both still inside the patch context.
            deadline = time.time() + 2
            while not run_ffmpeg.called and time.time() < deadline:
                time.sleep(0.02)

        self.assertTrue(post.called, "Gemini TTS endpoint was never called")
        request_url = post.call_args.args[0]
        request_body = post.call_args.kwargs["json"]
        prompt = request_body["contents"][0]["parts"][0]["text"]
        self.assertIn("gemini-2.5-pro-preview-tts", request_url)
        self.assertIn('Pronounce "LiDAR" as "lie-dar"', prompt)
        self.assertEqual(
            request_body["generationConfig"]["speechConfig"]["voiceConfig"][
                "prebuiltVoiceConfig"
            ]["voiceName"],
            "Sulafat",
        )
        self.assertTrue(run_ffmpeg.called)
        self.assertIn(job["status"], {"running", "done"})

    def test_security_headers_are_present(self):
        response = self.client.get("/get_settings")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")


class MachineIdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import web_app.app as app_module

        cls.module = app_module

    def test_macos_uses_stable_hardware_uuid_instead_of_network_guess(self):
        fake_ioreg_output = (
            '+-o Mac : <class IOPlatformExpertDevice>\n'
            '    "IOPlatformUUID" = "C5A1688F-DA50-5698-9B9C-0AF270F2E5EA"\n'
        )
        with patch.object(self.module.sys, "platform", "darwin"), \
             patch.object(self.module.os, "name", "posix"), \
             patch("subprocess.check_output", return_value=fake_ioreg_output.encode()):
            self.assertEqual(
                self.module.get_machine_id(), "C5A1688F-DA50-5698-9B9C-0AF270F2E5EA"
            )

    def test_macos_falls_back_when_ioreg_is_unavailable(self):
        with patch.object(self.module.sys, "platform", "darwin"), \
             patch.object(self.module.os, "name", "posix"), \
             patch("subprocess.check_output", side_effect=FileNotFoundError):
            machine_id = self.module.get_machine_id()
        self.assertTrue(machine_id.startswith("GEN-"))


if __name__ == "__main__":
    unittest.main()
