"""Local-only browser smoke-test server with external license I/O disabled."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import web_app.app as application


def fake_license():
    return True, {
        "email": "browser-test@example.com",
        "quota": "unlimited",
        "used": 0,
        "expiry_date": "Test",
        "expiry_time": "00:00",
    }


application.check_user_license = fake_license
application.app.config.update(TESTING=True)
application.app.run(debug=False, host="127.0.0.1", port=7504, use_reloader=False)
