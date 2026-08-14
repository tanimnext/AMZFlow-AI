import json
import os
import pathlib
import re
import time
import base64
import tempfile
import unittest
import uuid
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

    def test_get_settings_full_returns_the_saved_api_key_for_the_settings_page(self):
        # /get_settings (used by every other page) redacts secrets so a
        # saved key never round-trips into the DOM/network tab of a page
        # that doesn't need it. The Settings page itself is the one place a
        # saved key must show back up in its own field -- otherwise it looks
        # like saving silently failed every time the page reloads.
        with patch.object(self.module, "get_settings", return_value={"gemini_api_key": "secret-key-123", "logo_text": "Test"}):
            response = self.client.get("/get_settings_full")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["gemini_api_key"], "secret-key-123")
        self.assertEqual(payload["logo_text"], "Test")

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

    def test_keyword_failure_tracker_reports_a_fatal_keyword(self):
        track = self.module._track_keyword_failure
        keyword, failed = None, []
        keyword, failed = track("--- Keyword: best-robot-vacuums ---\n", keyword, failed)
        self.assertEqual(keyword, "best-robot-vacuums")
        self.assertEqual(failed, [])
        keyword, failed = track(
            "[FATAL] No products could be processed for 'best-robot-vacuums'\n", keyword, failed,
        )
        self.assertEqual(failed, ["best-robot-vacuums"])

    def test_keyword_failure_tracker_does_not_duplicate_repeated_fatal_lines(self):
        track = self.module._track_keyword_failure
        keyword, failed = "best-robot-vacuums", []
        keyword, failed = track("[FATAL] first problem\n", keyword, failed)
        keyword, failed = track("[FATAL] second problem, same keyword\n", keyword, failed)
        self.assertEqual(failed, ["best-robot-vacuums"])

    def test_keyword_failure_tracker_moves_on_to_the_next_keyword(self):
        track = self.module._track_keyword_failure
        keyword, failed = None, []
        keyword, failed = track("--- Keyword: keyword-one ---\n", keyword, failed)
        keyword, failed = track("[FATAL] keyword-one failed\n", keyword, failed)
        keyword, failed = track("--- Keyword: keyword-two ---\n", keyword, failed)
        keyword, failed = track("[SUCCESS] Video creation process finished for: keyword-two\n", keyword, failed)
        self.assertEqual(keyword, "keyword-two")
        self.assertEqual(failed, ["keyword-one"])

    def test_video_route_rejects_path_traversal(self):
        response = self.client.get("/video/../../etc/passwd")
        self.assertIn(response.status_code, (400, 404))

    def test_video_route_404s_when_no_video_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(self.module, "library_root", return_value=tmp):
                response = self.client.get("/video/nonexistent-project")
        self.assertEqual(response.status_code, 404)

    def test_open_folder_rejects_path_traversal(self):
        response = self.client.post(
            "/open_folder", json={"keyword": "../../etc"}, headers={"X-CSRF-Token": "test-csrf"},
        )
        self.assertEqual(response.status_code, 400)

    def test_open_folder_404s_for_a_missing_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(self.module, "library_root", return_value=tmp):
                response = self.client.post(
                    "/open_folder", json={"keyword": "nonexistent-project"},
                    headers={"X-CSRF-Token": "test-csrf"},
                )
        self.assertEqual(response.status_code, 404)

    def test_open_folder_shells_out_to_the_platform_file_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = os.path.realpath(tmp)  # macOS symlinks /var -> /private/var
            project_dir = os.path.join(tmp, "best-robot-vacuums")
            os.makedirs(project_dir)
            with patch.object(self.module, "library_root", return_value=tmp), \
                 patch.object(self.module.sys, "platform", "darwin"), \
                 patch.object(self.module, "subprocess") as subprocess_mock:
                response = self.client.post(
                    "/open_folder", json={"keyword": "best-robot-vacuums"},
                    headers={"X-CSRF-Token": "test-csrf"},
                )
        self.assertEqual(response.status_code, 200)
        subprocess_mock.run.assert_called_once_with(["open", project_dir], check=False)

    def test_content_batches_history_reports_missing_video_as_not_found(self):
        fake_job = {
            "jobId": "job1", "keyword": "Best Robot Vacuums",
            "sourceUrl": "https://reviews.example/robot-vacuums",
            "generatedAt": "2026-08-12T00:00:00",
            "renderStatus": "PROCESSING",
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
        self.assertEqual(row["jobId"], "job1")
        self.assertEqual(row["renderStatus"], "PROCESSING")

    def test_content_batches_history_prefers_an_existing_video_file_over_a_failed_status(self):
        # A slow render that finished successfully AFTER the run was
        # (incorrectly, or in a since-fixed edge case) recorded as FAILED
        # must not hide the Watch link -- the file on disk is ground truth.
        fake_job = {
            "jobId": "job2", "keyword": "Best Coffee Makers",
            "sourceUrl": "https://reviews.example/coffee-makers",
            "generatedAt": "2026-08-12T00:00:00",
            "renderStatus": "FAILED",
        }
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = os.path.join(tmp, "best-coffee-makers")
            os.makedirs(project_dir)
            with open(os.path.join(project_dir, "best-coffee-makers.mp4"), "wb") as f:
                f.write(b"fake video bytes")
            with patch.object(self.module, "library_root", return_value=tmp):
                store, _ = self.module._content_batch_services()
                with patch.object(store, "list_generated_jobs", return_value=[fake_job]):
                    response = self.client.get("/api/content-batches/history")
        row = response.get_json()["data"][0]
        self.assertTrue(row["hasVideo"])
        self.assertEqual(row["renderStatus"], "DONE")

    def _make_content_job(self, keyword="Best Robot Vacuums", url=None):
        # The content-batch DB is a real, persistent sqlite file (not a
        # tempdir) shared across test runs, and create_batch rejects a URL
        # that was already analyzed -- so each call needs its own URL or a
        # second test run collides with the previous run's leftover rows.
        url = url or f"https://reviews.example/route-test-{uuid.uuid4().hex}"
        store, _ = self.module._content_batch_services()
        batch = store.create_batch([url])
        job = batch["jobs"][0]
        store.complete_job(job["jobId"], {
            "keyword": keyword, "contentType": "ROUNDUP", "confidence": 90,
            "revenuePotential": "HIGH",
            "products": [{"asin": "B0RTESTABC", "name": "X", "isIncluded": True}],
        })
        return store, job["jobId"]

    def test_delete_content_job_route_removes_it(self):
        store, job_id = self._make_content_job()
        response = self.client.delete(
            f"/api/content-jobs/{job_id}", headers={"X-CSRF-Token": "test-csrf"}
        )
        self.assertEqual(response.status_code, 200)
        with self.assertRaises(KeyError):
            store.get_job(job_id)

    def test_delete_content_job_route_404s_for_unknown_id(self):
        response = self.client.delete(
            f"/api/content-jobs/{'0' * 32}", headers={"X-CSRF-Token": "test-csrf"}
        )
        self.assertEqual(response.status_code, 404)

    def test_bulk_delete_route_removes_the_given_jobs(self):
        store, job_id_1 = self._make_content_job("Kw One")
        _, job_id_2 = self._make_content_job("Kw Two")
        response = self.client.post(
            "/api/content-jobs/bulk-delete", json={"jobIds": [job_id_1, job_id_2]},
            headers={"X-CSRF-Token": "test-csrf"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["deleted"], 2)
        with self.assertRaises(KeyError):
            store.get_job(job_id_1)

    def test_bulk_delete_route_rejects_empty_list(self):
        response = self.client.post(
            "/api/content-jobs/bulk-delete", json={"jobIds": []},
            headers={"X-CSRF-Token": "test-csrf"},
        )
        self.assertEqual(response.status_code, 422)

    def test_regenerate_route_writes_the_keyword_file_and_resets_status(self):
        store, job_id = self._make_content_job("Best Robot Vacuums")
        store.update_job(job_id, {"isApproved": True})
        store.mark_generated(store.get_job(job_id)["batchId"])
        store.record_generation_results({"best-robot-vacuums"})  # -> FAILED
        self.assertEqual(store.get_job(job_id)["renderStatus"], "FAILED")

        response = self.client.post(
            f"/api/content-jobs/{job_id}/regenerate", headers={"X-CSRF-Token": "test-csrf"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(store.get_job(job_id)["renderStatus"], "PROCESSING")
        with open(self.module.KEYWORDS_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Best Robot Vacuums", content)
        self.assertIn("B0RTESTABC", content)

    def test_regenerate_route_404s_for_unknown_id(self):
        response = self.client.post(
            f"/api/content-jobs/{'0' * 32}/regenerate", headers={"X-CSRF-Token": "test-csrf"}
        )
        self.assertEqual(response.status_code, 404)

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
        self.assertIn('say "LiDAR" as "lie-dar"', prompt)
        self.assertEqual(
            request_body["generationConfig"]["speechConfig"]["voiceConfig"][
                "prebuiltVoiceConfig"
            ]["voiceName"],
            "Sulafat",
        )
        self.assertTrue(run_ffmpeg.called)
        self.assertIn(job["status"], {"running", "done"})

    def test_fallback_chain_textareas_are_actually_submitted_on_save(self):
        # collectSettings() in settings_page.js skips every element carrying
        # data-setting="false" (a marker for UI-only proxies like
        # #voice_select). Both chain textareas were tagged with it, so the
        # user's drag-ordered fallback order was silently dropped from every
        # save payload -- the toggle beside it persisted, the order never
        # did, which reads as "the chain resets itself".
        page = self.client.get("/settings").data.decode()
        for field in ("llm_chain", "tts_chain"):
            match = re.search(rf'<textarea id="{field}"[^>]*>', page)
            self.assertIsNotNone(match, f"#{field} textarea missing from settings page")
            self.assertNotIn(
                'data-setting="false"',
                match.group(0),
                f"#{field} is excluded from collectSettings() and will never be saved",
            )

    def test_settings_template_defines_both_fallback_chain_keys(self):
        # Server-side allow-list is derived from the shipped template, so a
        # chain key missing there is rejected as an "Unknown setting".
        settings_template = json.loads(
            (pathlib.Path(self.module.BASE_DIR) / "settings.json").read_text(encoding="utf-8")
        )
        for key in ("llm_chain", "tts_chain", "llm_fallback_enabled", "tts_fallback_enabled"):
            self.assertIn(key, settings_template)

    def test_save_handler_scopes_chain_row_queries_per_chain(self):
        # ".chain-row" matches BOTH the LLM and the voice fallback rows, but
        # only LLM rows contain a ".chain-model" input. An unscoped
        # `$$(".chain-row")` + `.querySelector(".chain-model").value` threw a
        # TypeError before the save request was ever built, so Save Changes
        # silently did nothing for anyone with a voice fallback configured.
        js = self.client.get("/static/js/settings_page.js").data.decode()
        self.assertNotIn('$$(".chain-row")', js)
        self.assertIn('$$("#llmChainRows .chain-row")', js)
        self.assertIn('$$("#ttsChainRows .chain-row")', js)

    def test_clearing_a_stored_secret_is_possible(self):
        # Blank alone means "keep the stored value" (the create page saves
        # from a redacted load), so removing a saved credential needs the
        # explicit signal -- otherwise a service-account JSON can never be
        # deleted once saved.
        self.module.save_settings({"vertex_service_account_private_key": '{"k":1}'})
        self.client.post(
            "/save_settings",
            json={"vertex_service_account_private_key": ""},
            headers={"X-CSRF-Token": "test-csrf"},
        )
        kept = self.client.get("/get_settings_full").get_json()
        self.assertEqual(kept["vertex_service_account_private_key"], '{"k":1}')

        self.client.post(
            "/save_settings",
            json={
                "vertex_service_account_private_key": "",
                "__cleared_secrets__": ["vertex_service_account_private_key"],
            },
            headers={"X-CSRF-Token": "test-csrf"},
        )
        cleared = self.client.get("/get_settings_full").get_json()
        self.assertEqual(cleared["vertex_service_account_private_key"], "")

    def test_cleared_secrets_cannot_blank_a_non_secret_setting(self):
        before = self.client.get("/get_settings_full").get_json()["intro_text"]
        self.client.post(
            "/save_settings",
            json={"__cleared_secrets__": ["intro_text"]},
            headers={"X-CSRF-Token": "test-csrf"},
        )
        after = self.client.get("/get_settings_full").get_json()["intro_text"]
        self.assertEqual(after, before)

    def test_security_headers_are_present(self):
        response = self.client.get("/get_settings")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")


class MachineIdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import web_app.app as app_module

        cls.module = app_module

    def setUp(self):
        # get_machine_id() is lru_cached (it is called on ~every request and
        # shells out to wmic on Windows). Each test here patches a different
        # platform, so the cache has to be dropped between them or the second
        # test just replays the first one's answer.
        self.module.get_machine_id.cache_clear()

    tearDown = setUp

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
