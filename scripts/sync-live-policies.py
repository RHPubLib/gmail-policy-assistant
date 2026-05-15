#!/usr/bin/env python3
"""Nightly sync from Director-maintained intranet Drive → Vertex AI Search.

Walks the two policy folders the Director republishes to:
  - Intranet/RHub/Personnel Policies and Guidelines
  - Intranet/RHub/Public Service Policies and Guidelines

Skips `Old Policies` subfolders and `application/vnd.google-apps.shortcut`
entries (which point at the Director's private editable Guidelines Docs we
don't have access to and shouldn't ingest).

For each remaining file:
  1. Check the local cache. If (file_id + modifiedTime) hasn't changed since
     the last run, reuse the cached MD.
  2. Otherwise download (PDFs as bytes; Google Docs exported as PDF), pipe
     through local docling-serve to produce clean Markdown, and cache.

Build a JSONL manifest where each line is a Discovery Engine Document with:
  - structData.drive_file_id  → so Apps Script citations link to the live doc
  - structData.title          → human-readable
  - structData.modified_time  → for change tracking
  - content.rawBytes          → base64 of the MD

Upload manifest + invoke documents:import on your-policies-datastore with
reconciliationMode=FULL so the index always exactly mirrors the source folders.

Designed for nightly cron. Idempotent. Safe to re-run anytime.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

KEY_PATH = Path.home() / ".config/policies-addon/sa-key.json"
PROJECT = "your-gcp-project"
DATA_STORE_ID = "your-policies-datastore"
GCS_BUCKET = "your-policies-bucket"
MANIFEST_OBJECT = "_manifest.jsonl"
DOCLING_URL = "http://localhost:5001/v1/convert/file"

ROOTS = {
    "Personnel Policies and Guidelines": "YOUR_PERSONNEL_FOLDER_ID",
    "Public Service Policies and Guidelines": "YOUR_PUBLIC_SERVICE_FOLDER_ID",
}
EXCLUDE_FOLDER_NAMES = {"Old Policies"}
INGESTIBLE_MIMES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.google-apps.document",
}

CACHE_DIR = Path.home() / ".cache/policies-addon-sync"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/cloud-platform",
]


# ---------------------------------------------------------------------------
# Drive helpers
# ---------------------------------------------------------------------------

def make_drive():
    creds = service_account.Credentials.from_service_account_file(
        str(KEY_PATH), scopes=SCOPES
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def walk_drive(drive, parent_id, path="", included=True):
    """Yield (path, file) for every non-folder file under parent_id,
    skipping EXCLUDE_FOLDER_NAMES subtrees."""
    page_token = None
    while True:
        resp = drive.files().list(
            q=f"'{parent_id}' in parents and trashed = false",
            fields=("nextPageToken, files(id, name, mimeType, modifiedTime, "
                    "shortcutDetails, size)"),
            supportsAllDrives=True, includeItemsFromAllDrives=True,
            pageToken=page_token, pageSize=200,
        ).execute()
        for f in resp.get("files", []):
            sub_path = path + "/" + f["name"]
            if f["mimeType"] == "application/vnd.google-apps.folder":
                child_inc = included and (f["name"] not in EXCLUDE_FOLDER_NAMES)
                yield from walk_drive(drive, f["id"], sub_path, child_inc)
            else:
                if included:
                    yield (sub_path, f)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break


def download_or_export(drive, f) -> bytes:
    """Get file bytes as PDF (for Docling). Google Docs are exported as PDF."""
    if f["mimeType"] == "application/vnd.google-apps.document":
        request = drive.files().export_media(
            fileId=f["id"], mimeType="application/pdf"
        )
    else:
        request = drive.files().get_media(fileId=f["id"], supportsAllDrives=True)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request, chunksize=1024 * 1024)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Docling
# ---------------------------------------------------------------------------

def docling_convert(file_bytes: bytes, filename: str) -> str:
    """POST to docling-serve /v1/convert/file. Returns Markdown."""
    files = {"files": (filename, file_bytes, "application/octet-stream")}
    data = {"to_formats": "md"}
    resp = requests.post(DOCLING_URL, files=files, data=data, timeout=300)
    resp.raise_for_status()
    j = resp.json()
    # Response: {"document": {"md_content": "...", ...}, "status": "success"}
    doc = j.get("document") or {}
    md = doc.get("md_content")
    if not md:
        raise RuntimeError(f"docling-serve returned no md_content: {j!r}")
    return md


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def cache_path_for(f) -> Path:
    """Cache key = file_id + modifiedTime. Bumping mod time forces re-convert."""
    key = f["id"] + "_" + f.get("modifiedTime", "")
    h = hashlib.sha256(key.encode()).hexdigest()
    return CACHE_DIR / f"{h}.md"


# ---------------------------------------------------------------------------
# GCS + Discovery Engine
# ---------------------------------------------------------------------------

def upload_manifest_to_gcs(manifest_path: Path):
    """Upload via `gcloud storage cp` to avoid depending on the
    google-cloud-storage client lib (not always installed)."""
    import subprocess
    dest = f"gs://{GCS_BUCKET}/{MANIFEST_OBJECT}"
    subprocess.run(
        ["gcloud", "storage", "cp", str(manifest_path), dest,
         "--project", PROJECT, "--content-type=application/jsonl"],
        check=True, capture_output=True,
    )
    return dest


def trigger_import(manifest_uri: str) -> str:
    """Call Discovery Engine documents:import. Returns operation name."""
    creds = service_account.Credentials.from_service_account_file(
        str(KEY_PATH), scopes=SCOPES
    )
    creds.refresh(__import__("google.auth.transport.requests",
                             fromlist=["Request"]).Request())
    url = (f"https://discoveryengine.googleapis.com/v1"
           f"/projects/{PROJECT}/locations/global/collections/default_collection"
           f"/dataStores/{DATA_STORE_ID}/branches/0/documents:import")
    body = {
        "gcsSource": {"inputUris": [manifest_uri], "dataSchema": "document"},
        "reconciliationMode": "FULL",
    }
    r = requests.post(
        url, headers={"Authorization": f"Bearer {creds.token}",
                      "X-Goog-User-Project": PROJECT},
        json=body, timeout=60,
    )
    r.raise_for_status()
    return r.json()["name"]


def poll_import(operation_name: str, timeout_s: int = 600) -> dict:
    creds = service_account.Credentials.from_service_account_file(
        str(KEY_PATH), scopes=SCOPES
    )
    creds.refresh(__import__("google.auth.transport.requests",
                             fromlist=["Request"]).Request())
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = requests.get(
            f"https://discoveryengine.googleapis.com/v1/{operation_name}",
            headers={"Authorization": f"Bearer {creds.token}",
                     "X-Goog-User-Project": PROJECT},
            timeout=30,
        )
        r.raise_for_status()
        j = r.json()
        if j.get("done"):
            return j
        time.sleep(10)
    raise TimeoutError(f"Import operation {operation_name} did not complete in {timeout_s}s")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true",
                   help="Process files but don't upload/import. Useful for testing.")
    p.add_argument("--limit", type=int, default=0,
                   help="Process at most N files (for quick tests).")
    p.add_argument("--skip-import", action="store_true",
                   help="Build the manifest but don't trigger Discovery Engine import.")
    args = p.parse_args()

    drive = make_drive()

    print("Walking source folders…", flush=True)
    items = []
    for root_name, root_id in ROOTS.items():
        for path, f in walk_drive(drive, root_id, root_name):
            if f["mimeType"] not in INGESTIBLE_MIMES:
                continue  # silently skip shortcuts and other types
            items.append((path, f))

    if args.limit:
        items = items[: args.limit]
    print(f"In-scope files: {len(items)}", flush=True)

    manifest_lines = []
    cache_hits = 0
    converted = 0
    failed = []

    for i, (path, f) in enumerate(items, 1):
        cache = cache_path_for(f)
        print(f"  [{i:3d}/{len(items)}] {path}", flush=True)
        if cache.exists():
            md = cache.read_text(encoding="utf-8")
            cache_hits += 1
            print(f"        cache hit ({len(md):,} chars)", flush=True)
        else:
            try:
                file_bytes = download_or_export(drive, f)
                md = docling_convert(file_bytes, f["name"])
            except Exception as e:
                print(f"        FAILED: {type(e).__name__}: {e}", flush=True)
                failed.append((path, str(e)))
                continue
            cache.write_text(md, encoding="utf-8")
            converted += 1
            print(f"        converted ({len(md):,} chars)", flush=True)

        if not md.strip():
            print(f"        SKIP (empty Markdown)", flush=True)
            failed.append((path, "empty md"))
            continue

        doc_id = "doc_" + hashlib.md5(f["id"].encode()).hexdigest()
        manifest_lines.append(json.dumps({
            "id": doc_id,
            "structData": {
                "title": Path(f["name"]).stem,
                "filename": f["name"],
                "drive_file_id": f["id"],
                "drive_url": f"https://drive.google.com/file/d/{f['id']}/view",
                "source_path": path,
                "modified_time": f.get("modifiedTime", ""),
            },
            "content": {
                "mimeType": "text/plain",
                "rawBytes": base64.b64encode(md.encode("utf-8")).decode("ascii"),
            },
        }))

    print(f"\nSummary: in-scope={len(items)}  cache_hits={cache_hits}  "
          f"converted={converted}  failed={len(failed)}  manifest_docs={len(manifest_lines)}")
    if failed:
        print("Failures:")
        for path, err in failed:
            print(f"  - {path}: {err}")

    manifest_path = CACHE_DIR / "manifest.jsonl"
    manifest_path.write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    print(f"\nWrote manifest: {manifest_path} ({manifest_path.stat().st_size:,} bytes)")

    if args.dry_run:
        print("Dry run — not uploading or importing.")
        return 0

    print("\nUploading manifest to GCS…", flush=True)
    manifest_uri = upload_manifest_to_gcs(manifest_path)
    print(f"  → {manifest_uri}")

    if args.skip_import:
        print("--skip-import set — leaving manifest in place; not invoking import.")
        return 0

    print("\nTriggering Discovery Engine import (reconciliationMode=FULL)…", flush=True)
    op = trigger_import(manifest_uri)
    print(f"  operation: {op}")
    print("Polling for completion (up to 10 minutes)…")
    result = poll_import(op)
    meta = result.get("metadata", {})
    print(f"  successCount: {meta.get('successCount')}  "
          f"failureCount: {meta.get('failureCount', 0)}")
    errs = result.get("response", {}).get("errorSamples", [])
    for e in errs[:3]:
        print(f"  error sample: {e.get('message', '')[:240]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
