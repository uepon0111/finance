#!/usr/bin/env python3
"""Generate an OAuth refresh-token record for one Google Drive account.

This is a one-time local setup helper. Do not commit the generated JSON file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPE = "https://www.googleapis.com/auth/drive.file"


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a refresh token for the TQQQ Candle Lab Drive backup.")
    parser.add_argument("--client-secrets", default="client_secret.json", help="Google OAuth desktop client JSON file")
    parser.add_argument("--label", default="drive-account-1", help="Friendly label for the account")
    parser.add_argument("--output", default="drive-account.json", help="Output JSON file (keep this private)")
    args = parser.parse_args()

    client_path = Path(args.client_secrets)
    if not client_path.exists():
        raise SystemExit(f"OAuth client JSON not found: {client_path}")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_path), scopes=[SCOPE])
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    if not credentials.refresh_token:
        raise SystemExit("Google did not return a refresh token. Re-run with consent or review OAuth setup.")

    client_config = flow.client_config
    record = {
        "label": args.label,
        "client_id": client_config.get("client_id"),
        "client_secret": client_config.get("client_secret"),
        "refresh_token": credentials.refresh_token,
    }
    output = Path(args.output)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved one private Drive account record to: {output}")
    print("Do not commit this file. Copy its JSON fields into the GitHub Actions secret DRIVE_ACCOUNTS_JSON.")


if __name__ == "__main__":
    main()
