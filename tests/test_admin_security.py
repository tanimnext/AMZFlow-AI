import os
import sys
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
