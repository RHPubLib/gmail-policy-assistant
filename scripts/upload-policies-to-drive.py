#!/usr/bin/env python3
"""Upload converted policy markdowns to the RHPL Policies KB Shared Drive.

Idempotent: skips files/folders that already exist by name under the same
parent. Re-run after running `docling-batch-convert.py` and `owui-reload-kb.py`
to keep the Drive copy in sync with the OWUI KB.

Requires:
  - SA key at ~/.config/policies-addon/sa-key.json
  - The policies-addon SA must be a Manager on the target Shared Drive

Usage:
  python3 scripts/upload-policies-to-drive.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

KEY_PATH = Path.home() / ".config/policies-addon/sa-key.json"
SHARED_DRIVE_ID = "0ADfIkowMXptsUk9PVA"
SOURCE_DIR = Path("~/local-ai/kb-converted")
SKIP_FILENAMES = {"upload.log"}
SCOPES = ["https://www.googleapis.com/auth/drive"]


def main() -> int:
    creds = service_account.Credentials.from_service_account_file(
        str(KEY_PATH), scopes=SCOPES
    )
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)

    def list_children(parent_id: str) -> dict[str, dict]:
        """Return {name: {id, mimeType}} for all non-trashed children of parent_id."""
        out, page_token = {}, None
        while True:
            resp = drive.files().list(
                q=f"'{parent_id}' in parents and trashed = false",
                fields="nextPageToken, files(id, name, mimeType)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                corpora="drive",
                driveId=SHARED_DRIVE_ID,
                pageToken=page_token,
                pageSize=200,
            ).execute()
            for f in resp.get("files", []):
                out[f["name"]] = f
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return out

    def get_or_create_folder(name: str, parent_id: str, existing: dict) -> str:
        if name in existing and existing[name]["mimeType"] == "application/vnd.google-apps.folder":
            return existing[name]["id"]
        body = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        f = drive.files().create(body=body, supportsAllDrives=True, fields="id").execute()
        return f["id"]

    # path-to-drive-id map, seeded with the source dir → Shared Drive root
    path_to_id: dict[Path, str] = {SOURCE_DIR: SHARED_DRIVE_ID}
    folders_created = 0
    files_uploaded = 0
    files_skipped = 0

    for root, dirs, files in os.walk(SOURCE_DIR):
        root_path = Path(root)
        parent_id = path_to_id[root_path]
        existing = list_children(parent_id)

        for d in sorted(dirs):
            sub_path = root_path / d
            before = d in existing
            sub_id = get_or_create_folder(d, parent_id, existing)
            path_to_id[sub_path] = sub_id
            if not before:
                folders_created += 1
                print(f"  [+folder] {sub_path.relative_to(SOURCE_DIR)}")

        for fname in sorted(files):
            if fname in SKIP_FILENAMES or not fname.endswith(".md"):
                continue
            if fname in existing:
                files_skipped += 1
                continue
            local = root_path / fname
            media = MediaFileUpload(str(local), mimetype="text/markdown")
            body = {"name": fname, "parents": [parent_id]}
            drive.files().create(
                body=body, media_body=media,
                supportsAllDrives=True, fields="id",
            ).execute()
            files_uploaded += 1
            rel = local.relative_to(SOURCE_DIR)
            print(f"  [+file]   {rel}")

    print(
        f"\nDone. folders_created={folders_created}  "
        f"files_uploaded={files_uploaded}  files_already_present={files_skipped}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
