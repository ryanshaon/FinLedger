"""What is this file, really? Magic bytes decide, never the client's Content-Type or extension.
Also: filename hygiene and ZIP splitting with bomb limits."""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass

PDF, JPEG, PNG, ZIP, XLSX, CSV = (
    "application/pdf", "image/jpeg", "image/png", "application/zip",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "text/csv",
)
EXT = {PDF: "pdf", JPEG: "jpg", PNG: "png", ZIP: "zip", XLSX: "xlsx", CSV: "csv"}

# What a vendor may send through a door. ZIP is a container, never stored as a document itself.
DOOR_TYPES = {PDF, JPEG, PNG, ZIP, XLSX}

ZIP_MAX_MEMBERS = 20
ZIP_MAX_MEMBER_BYTES = 25 * 1024 * 1024
ZIP_MAX_TOTAL_BYTES = 60 * 1024 * 1024


def sniff(data: bytes) -> str | None:
    if data[:5] == b"%PDF-" or (data[:1024].find(b"%PDF-") != -1):  # some generators prepend junk
        return PDF
    if data[:3] == b"\xff\xd8\xff":
        return JPEG
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return PNG
    if data[:4] == b"PK\x03\x04":
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            return None
        return XLSX if "[Content_Types].xml" in names and any(n.startswith("xl/") for n in names) else ZIP
    return None


def safe_filename(name: str, mime: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").replace("\\", "/").rsplit("/", 1)[-1]).strip("._")[:80]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return f"{stem or 'file'}.{EXT[mime]}"


@dataclass
class Part:
    filename: str
    data: bytes
    mime: str
    archive: str | None = None  # name of the ZIP it came out of


@dataclass
class Rejected:
    filename: str
    reason: str


def explode(filename: str, data: bytes) -> tuple[list[Part], list[Rejected]]:
    """One uploaded blob -> document-sized parts. ZIPs are split one level deep; nested ZIPs are refused."""
    mime = sniff(data)
    if mime is None or mime not in DOOR_TYPES:
        return [], [Rejected(filename, "unsupported file type (send PDF, JPG, PNG, XLSX or ZIP)")]
    if mime != ZIP:
        return [Part(filename, data, mime)], []

    parts: list[Part] = []
    rejected: list[Rejected] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        members = [i for i in zf.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX/")
                   and not i.filename.rsplit("/", 1)[-1].startswith(".")]
    except zipfile.BadZipFile:
        return [], [Rejected(filename, "corrupt ZIP")]
    if len(members) > ZIP_MAX_MEMBERS:
        return [], [Rejected(filename, f"ZIP has more than {ZIP_MAX_MEMBERS} files")]
    total = 0
    for info in members:
        if info.flag_bits & 0x1:
            rejected.append(Rejected(info.filename, "encrypted ZIP entry"))
            continue
        # Read with a hard cap instead of trusting the header's declared size (zip bombs lie).
        with zf.open(info) as fh:
            blob = fh.read(ZIP_MAX_MEMBER_BYTES + 1)
        total += len(blob)
        if len(blob) > ZIP_MAX_MEMBER_BYTES or total > ZIP_MAX_TOTAL_BYTES:
            return [], [Rejected(filename, "ZIP too large when expanded")]
        inner = sniff(blob)
        if inner is None or inner not in DOOR_TYPES:
            rejected.append(Rejected(info.filename, "unsupported file type inside ZIP"))
        elif inner == ZIP:
            rejected.append(Rejected(info.filename, "nested ZIP not accepted"))
        else:
            parts.append(Part(info.filename, blob, inner, archive=filename))
    return parts, rejected
