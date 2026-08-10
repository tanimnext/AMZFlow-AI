import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web_app"))
TEST_DATA = tempfile.TemporaryDirectory()
os.environ["EZ_AMAZTUBE_DATA_DIR"] = TEST_DATA.name

import admin_app


class AdminSecurityTests(unittest.TestCase):
    def setUp(self):
        admin_app.app.config.update(TESTING=True)
        self.client = admin_app.app.test_client()

    def test_login_post_requires_csrf(self):
        response = self.client.post("/login", data={"password": "anything"})
        self.assertEqual(response.status_code, 403)

    def test_login_page_contains_csrf_token(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="csrf_token"', response.data)

    def test_machine_reset_confirms_the_email_can_activate_again(self):
        with self.client.session_transaction() as session:
            session["is_admin"] = True
            session["csrf_token"] = "csrf-test"
        with patch.object(
            admin_app.license_store,
            "reset_machine",
            return_value=(True, "Machine binding reset. They can activate again with just their email."),
        ), patch.object(admin_app.license_store, "list_users", return_value=[]):
            response = self.client.post(
                "/users/user@example.com/reset-machine",
                data={"csrf_token": "csrf-test"},
                follow_redirects=True,
            )
        self.assertIn(b"activate again", response.data)


if __name__ == "__main__":
    unittest.main()
