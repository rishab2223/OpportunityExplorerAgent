from __future__ import annotations

import io
import re
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
]


def parse_drive_file_id(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", value)
    if match:
        return match.group(1)
    match = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", value)
    if match:
        return match.group(1)
    return value


def google_credentials(credentials_path: str) -> Credentials:
    path = Path(credentials_path)
    if not credentials_path or not path.exists():
        raise FileNotFoundError(
            "GOOGLE_APPLICATION_CREDENTIALS is missing or the file does not exist"
        )
    return Credentials.from_service_account_file(str(path), scopes=DRIVE_SCOPES)


def download_drive_file(credentials_path: str, file_id_or_url: str) -> tuple[bytes, str]:
    file_id = parse_drive_file_id(file_id_or_url)
    if not file_id:
        raise ValueError("Google Drive file id is empty")
    creds = google_credentials(credentials_path)
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    meta = service.files().get(fileId=file_id, fields="name,mimeType").execute()
    name = meta.get("name") or f"{file_id}.bin"
    mime = meta.get("mimeType") or ""
    if mime.startswith("application/vnd.google-apps"):
        export_mime = "text/plain"
        if "document" in mime:
            export_mime = "application/pdf"
            name = Path(name).stem + ".pdf"
        request = service.files().export_media(fileId=file_id, mimeType=export_mime)
    else:
        request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue(), name
