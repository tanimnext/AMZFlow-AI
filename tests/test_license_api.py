import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from web_app import license_store


class LicenseApiClientTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.activation_file = Path(self.temp_dir.name) / ".activated"
        self.environment = patch.dict(
            os.environ,
            {"AMZFLOW_LICENSE_API_URL": "https://license.example.workers.dev"},
            clear=False,
        )
        self.environment.start()
        self.file_patch = patch.object(license_store, "ACTIVATION_FILE", self.activation_file)
        self.file_patch.start()

    def tearDown(self):
        self.file_patch.stop()
        self.environment.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def response(status, payload):
        response = Mock()
        response.status_code = status
        response.json.return_value = payload
        return response

    @patch("web_app.license_store.requests.request")
    def test_clean_install_activates_without_google_credentials(self, request):
        request.return_value = self.response(200, {"data": {
            "activationToken": "v1.payload.signature",
            "license": {"email": "user@example.com", "name": "Customer", "used": 0,
                        "quota": 10, "expiryDate": "Lifetime", "expiryTime": "00:00"},
        }})

        success, result = license_store.activate_license(
            "user@example.com", "Customer", "machine-id-123", "abcd-efgh-2345-6789"
        )

        self.assertTrue(success)
        self.assertEqual(result["quota"], 10)
        saved = json.loads(self.activation_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["activation_token"], "v1.payload.signature")
        self.assertEqual(saved["machine_id"], "machine-id-123")
        self.assertEqual(self.activation_file.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("google", self.activation_file.read_text(encoding="utf-8").lower())

    @patch("web_app.license_store.requests.request")
    def test_verification_uses_private_activation_token(self, request):
        self.activation_file.write_text(json.dumps({
            "email": "user@example.com", "machine_id": "machine-id-123",
            "activation_token": "v1.payload.signature",
        }), encoding="utf-8")
        request.return_value = self.response(200, {"data": {"license": {
            "email": "user@example.com", "name": "Customer", "used": 2,
            "quota": "Unlimited", "expiryDate": "Lifetime", "expiryTime": "00:00",
        }}})

        success, result = license_store.verify_activation_local("user@example.com", "machine-id-123")

        self.assertTrue(success)
        self.assertEqual(result["used"], 2)
        headers = request.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer v1.payload.signature")

    def test_missing_api_url_never_reports_database_connection_error(self):
        with patch.dict(os.environ, {"AMZFLOW_LICENSE_API_URL": ""}, clear=False), \
             patch("web_app.license_store.resource_dir", return_value=Path(self.temp_dir.name)):
            success, message = license_store.activate_license(
                "user@example.com", "Customer", "machine-id-123", "abcd-efgh-2345-6789"
            )
        self.assertFalse(success)
        self.assertNotIn("Database Connection Error", message)
        self.assertIn("not configured", message.lower())

    @patch("web_app.license_store.requests.request")
    def test_public_error_does_not_leak_server_details(self, request):
        request.return_value = self.response(500, {"error": {
            "code": "INTERNAL_ERROR", "message": "The service is temporarily unavailable."
        }})
        success, message = license_store.activate_license(
            "user@example.com", "Customer", "machine-id-123", "wrong-code"
        )
        self.assertFalse(success)
        self.assertEqual(message, "The service is temporarily unavailable.")


class ActivationRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import web_app.app as app_module
        cls.module = app_module
        app_module.app.config.update(TESTING=True)

    def test_activation_page_requires_one_time_code(self):
        client = self.module.app.test_client()
        response = client.get("/activate")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="activationCode"', response.data)

    def test_activation_route_passes_code_to_server_api_client(self):
        client = self.module.app.test_client()
        with client.session_transaction() as session:
            session["csrf_token"] = "csrf-test"
        license = {
            "email": "user@example.com", "name": "Customer", "used": 0,
            "quota": 10, "expiry_date": "Lifetime", "expiry_time": "00:00",
        }
        with patch.object(self.module.license_store, "activate_license", return_value=(True, license)) as activate:
            response = client.post(
                "/activate",
                json={"email": "user@example.com", "name": "Customer", "activationCode": "abcd-efgh-2345-6789"},
                headers={"X-CSRF-Token": "csrf-test"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        activate.assert_called_once_with(
            "user@example.com", "Customer", unittest.mock.ANY, "abcd-efgh-2345-6789"
        )


if __name__ == "__main__":
    unittest.main()
