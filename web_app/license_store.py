"""Google-Sheets-backed license/user store.

Uses the service account from the private application-data directory and the
configured Google Sheet — credentials never need to live in the source tree.

Sheet columns (row 1 = header): A=Name, B=Email, C=MachineID, D=LastLogin,
E=Used, F=Quota, G=ExpiryDate, H=ExpiryTime
"""
import os
from datetime import datetime

from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
from secure_paths import SERVICE_ACCOUNT_FILE

CONFIG_PATH = str(SERVICE_ACCOUNT_FILE)

# From: https://docs.google.com/spreadsheets/d/1OGbP51wSvk4W3_FlNtmuncRTpmaxTJ5qpLEfMyLP5OY/edit
SHEET_ID = "1OGbP51wSvk4W3_FlNtmuncRTpmaxTJ5qpLEfMyLP5OY"
RANGE = "Sheet1!A:H"


GOOGLE_SHEETS_TIMEOUT_SECONDS = 12


class _RequestsHttpAdapter:
    """Swap httplib2 for requests to avoid SSL issues on some Windows setups.

    Every call through this adapter now has a bounded timeout. Without one,
    `requests` waits forever by default -- and since every page view calls
    verify_activation_local(), a single unreachable/slow connection to
    sheets.googleapis.com (offline, firewalled, DNS hiccup) hung EVERY page
    load in the whole app for as long as the underlying transport happened to
    wait (observed: 120s per request) before finally redirecting.
    """
    def __init__(self, session):
        self.session = session

    def request(self, uri, method="GET", body=None, headers=None, **kwargs):
        kwargs.setdefault("timeout", GOOGLE_SHEETS_TIMEOUT_SECONDS)
        resp = self.session.request(method=method, url=uri, data=body, headers=headers, **kwargs)
        class ResponseWrapper(dict):
            def __init__(self, r):
                super().__init__({k.lower(): v for k, v in r.headers.items()})
                self.status = r.status_code
                self.reason = r.reason
        return ResponseWrapper(resp), resp.content


def _get_service():
    if not os.path.exists(CONFIG_PATH):
        print(f"[CRITICAL] {CONFIG_PATH} missing. Add your own service-account JSON key here.")
        return None
    try:
        creds = service_account.Credentials.from_service_account_file(
            CONFIG_PATH,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        session_obj = AuthorizedSession(creds)
        adapter = _RequestsHttpAdapter(session_obj)
        return build('sheets', 'v4', http=adapter, static_discovery=True)
    except Exception as e:
        print(f"[CRITICAL] Error building Sheets service: {e}")
        return None


def _row_to_user(row):
    return {
        "name": row[0].strip() if len(row) > 0 else "",
        "email": row[1].strip().lower() if len(row) > 1 else "",
        "machine_id": row[2].strip() if len(row) > 2 else "",
        "last_login": row[3].strip() if len(row) > 3 else "",
        "used": row[4].strip() if len(row) > 4 and row[4].strip() else "0",
        "quota": row[5].strip() if len(row) > 5 and row[5].strip() else "Unlimited",
        "expiry_date": row[6].strip() if len(row) > 6 else "",
        "expiry_time": row[7].strip() if len(row) > 7 else "00:00",
    }


def _fetch_rows(service):
    result = service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=RANGE).execute()
    return result.get('values', [])


def list_users():
    service = _get_service()
    if not service:
        return []
    try:
        rows = _fetch_rows(service)
        users = []
        for i, row in enumerate(rows):
            if i == 0:
                continue
            if len(row) < 2 or not row[1].strip():
                continue
            users.append(_row_to_user(row))
        return users
    except Exception as e:
        print(f"[ERROR] list_users: {e}")
        return []


def find_user(email):
    email_clean = email.strip().lower()
    for u in list_users():
        if u['email'] == email_clean:
            return u
    return None


def add_user(name, email, expiry_date="Lifetime", expiry_time="00:00", quota="Unlimited"):
    service = _get_service()
    if not service:
        return False, "Database Connection Error"
    try:
        email_clean = email.strip().lower()
        if find_user(email_clean):
            return False, "User with this email already exists"

        row = [name.strip(), email_clean, "", "", "0", quota or "Unlimited",
               expiry_date or "Lifetime", expiry_time or "00:00"]
        service.spreadsheets().values().append(
            spreadsheetId=SHEET_ID,
            range=RANGE,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]}
        ).execute()
        return True, "User added"
    except Exception as e:
        print(f"[ERROR] add_user: {e}")
        return False, f"System Error: {e}"


_FIELD_COLUMN = {
    "name": "A",
    "machine_id": "C",
    "last_login": "D",
    "used": "E",
    "quota": "F",
    "expiry_date": "G",
    "expiry_time": "H",
}


def _find_row_index(service, email_clean):
    rows = _fetch_rows(service)
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if len(row) > 1 and row[1].strip().lower() == email_clean:
            return i, row
    return None, None


def update_user(email, **fields):
    service = _get_service()
    if not service:
        return False, "Database Connection Error"
    try:
        email_clean = email.strip().lower()
        idx, _ = _find_row_index(service, email_clean)
        if idx is None:
            return False, "User not found"

        for field, value in fields.items():
            col = _FIELD_COLUMN.get(field)
            if not col:
                continue
            service.spreadsheets().values().update(
                spreadsheetId=SHEET_ID,
                range=f"Sheet1!{col}{idx + 1}",
                valueInputOption="USER_ENTERED",
                body={"values": [[value]]}
            ).execute()
        return True, "Updated"
    except Exception as e:
        print(f"[ERROR] update_user: {e}")
        return False, f"System Error: {e}"


def delete_user(email):
    service = _get_service()
    if not service:
        return False, "Database Connection Error"
    try:
        email_clean = email.strip().lower()
        idx, _ = _find_row_index(service, email_clean)
        if idx is None:
            return False, "User not found"

        service.spreadsheets().values().clear(
            spreadsheetId=SHEET_ID,
            range=f"Sheet1!A{idx + 1}:H{idx + 1}",
            body={}
        ).execute()
        return True, "Deleted"
    except Exception as e:
        print(f"[ERROR] delete_user: {e}")
        return False, f"System Error: {e}"


def reset_machine(email):
    return update_user(email, machine_id="")


def reset_usage(email):
    return update_user(email, used="0")


def update_usage(email, count):
    return update_user(email, used=str(count))


def verify_activation_local(email, current_machine_id, user_name_input=None):
    service = _get_service()
    if not service:
        return False, "Database Connection Error"

    try:
        rows = _fetch_rows(service)
        if not rows:
            return False, "Activation list is empty"

        email_clean = email.strip().lower()

        for i, row in enumerate(rows):
            if i == 0:
                continue
            if len(row) < 2:
                continue

            sheet_name = row[0].strip() if len(row) > 0 else ""
            sheet_email = row[1].strip().lower()
            if sheet_email != email_clean:
                continue

            sheet_machine = row[2].strip() if len(row) > 2 else ""
            sheet_used = row[4].strip() if len(row) > 4 and row[4].strip() else "0"
            sheet_quota = row[5].strip() if len(row) > 5 and row[5].strip() else "Unlimited"
            sheet_expiry_date = row[6] if len(row) > 6 else ""
            sheet_expiry_time = row[7] if len(row) > 7 else "00:00"

            if user_name_input and not sheet_name:
                service.spreadsheets().values().update(
                    spreadsheetId=SHEET_ID,
                    range=f"Sheet1!A{i + 1}",
                    valueInputOption="USER_ENTERED",
                    body={"values": [[user_name_input]]}
                ).execute()
                sheet_name = user_name_input

            if not sheet_machine:
                service.spreadsheets().values().update(
                    spreadsheetId=SHEET_ID,
                    range=f"Sheet1!C{i + 1}",
                    valueInputOption="USER_ENTERED",
                    body={"values": [[current_machine_id]]}
                ).execute()
            elif sheet_machine.lower() != current_machine_id.lower():
                return False, "Device Mismatch. Locked to another computer."

            if sheet_expiry_date and sheet_expiry_date.lower() != "lifetime":
                try:
                    expiry_dt = datetime.strptime(f"{sheet_expiry_date} {sheet_expiry_time}", "%Y-%m-%d %H:%M")
                    if datetime.now() > expiry_dt:
                        return False, f"Subscription Expired on {sheet_expiry_date}"
                except ValueError:
                    pass

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            service.spreadsheets().values().update(
                spreadsheetId=SHEET_ID,
                range=f"Sheet1!D{i + 1}",
                valueInputOption="USER_ENTERED",
                body={"values": [[now_str]]}
            ).execute()

            return True, {
                "email": email_clean,
                "name": sheet_name or email_clean.split('@')[0],
                "quota": sheet_quota,
                "used": sheet_used,
                "expiry_date": sheet_expiry_date,
                "expiry_time": sheet_expiry_time,
            }

        return False, "Email not found in authorized list."
    except Exception as e:
        print(f"[ERROR] verify_activation_local: {e}")
        return False, f"System Error: {e}"
