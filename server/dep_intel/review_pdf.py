"""Deterministic text PDF for a stored review.

No external renderer is installed in this environment. Pages are uncompressed
so tests can read the decision and review id from the bytes.
"""

from __future__ import annotations


def render_text_pdf(lines: list[str]) -> bytes:
    wrapped: list[str] = []
    for line in lines:
        text = _latin(line)
        if not text:
            wrapped.append("")
            continue
        while len(text) > 90:
            wrapped.append(text[:90])
            text = text[90:]
        wrapped.append(text)
    if not wrapped:
        wrapped = ["Nami Trace"]
    pages: list[list[str]] = []
    chunk: list[str] = []
    for line in wrapped:
        chunk.append(line)
        if len(chunk) >= 46:
            pages.append(chunk)
            chunk = []
    if chunk:
        pages.append(chunk)
    return _document(pages)


def _latin(value: str) -> str:
    cleaned = value.replace("\t", " ").replace("\r", "")
    return cleaned.encode("latin-1", errors="replace").decode("latin-1")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _stream(lines: list[str]) -> bytes:
    commands = ["BT", "/F1 11 Tf", "50 780 Td", "14 TL"]
    for line in lines:
        commands.append(f"({_escape(line)}) Tj")
        commands.append("T*")
    commands.append("ET")
    return "\n".join(commands).encode("latin-1")


def _document(pages: list[list[str]]) -> bytes:
    streams = [_stream(page) for page in pages]
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{4 + index * 2} 0 R" for index in range(len(pages)))
    objects.append(f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>".encode("ascii"))
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index, stream in enumerate(streams):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + index * 2} 0 R /Resources << /Font << /F1 3 0 R >> >> >>".encode("ascii")
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream")
    body = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, payload in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{number} 0 obj\n".encode("ascii"))
        body.extend(payload)
        body.extend(b"\nendobj\n")
    xref = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    body.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    body.extend(f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(body)
