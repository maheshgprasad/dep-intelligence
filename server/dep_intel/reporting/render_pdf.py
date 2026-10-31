"""White-page PDF for a stored intelligence report.

The page uses Carbon's light (g10) colors: white ground, gray text,
a blue accent, and hairline rules. Review exports keep their own renderer.
"""

from __future__ import annotations

from typing import Any

PAGE_W = 612
PAGE_H = 792
MARGIN = 48
CONTENT_W = PAGE_W - MARGIN * 2
TABLE_CAP = 24

INK = (22 / 255, 22 / 255, 22 / 255)
MUTED = (82 / 255, 82 / 255, 82 / 255)
BLUE = (15 / 255, 98 / 255, 254 / 255)
LINE = (224 / 255, 224 / 255, 224 / 255)
GREEN = (25 / 255, 128 / 255, 56 / 255)
AMBER = (142 / 255, 106 / 255, 0 / 255)
RED = (218 / 255, 30 / 255, 40 / 255)
WHITE = (1.0, 1.0, 1.0)

_STATUS_NAMES = {
    "structural": "Structural intelligence",
    "history": "History",
    "contracts": "Contracts",
    "health": "Health",
}


def render_report_pdf(document: dict[str, Any]) -> bytes:
    canvas = _Canvas()
    summary = document.get("executive_summary") or {}
    scope = document.get("scope")
    canvas.title(str(summary.get("headline") or "Intelligence report"))
    canvas.muted("Workspace intelligence report" if scope == "cluster" else "Repository intelligence report")
    canvas.status_line("Readiness", str(summary.get("readiness_label") or "Partial"), str(summary.get("readiness") or ""))
    canvas.gap(4)
    canvas.paragraph(str(summary.get("summary") or ""))
    generated = document.get("generated_at") or ""
    graph = (document.get("reproducibility") or {}).get("graph_version") or ""
    canvas.muted(f"Generated {generated}. Graph {graph}.")
    canvas.muted("This report describes published Nami intelligence. It is not a Boundary Gate decision for a specific change.")
    _attention(canvas, document)

    canvas.section("Intelligence status")
    status = document.get("intelligence_status") or {}
    for key in ("structural", "history", "contracts", "health"):
        item = status.get(key) or {}
        canvas.status_line(_STATUS_NAMES[key], str(item.get("label") or "Unavailable"), str(item.get("status") or ""))

    if scope == "cluster":
        _cluster(canvas, document)
    else:
        _repository(canvas, document)

    canvas.section("How to interpret this report")
    canvas.bullet("Dependency reach is not a probability of failure.")
    canvas.bullet("Historical change association does not prove a structural dependency.")
    canvas.bullet("Workspace and repository readiness is not a Boundary Gate decision.")
    canvas.bullet("Missing or unscanned data is reported separately from clean results.")
    canvas.section("Limitations")
    limitations = [str(item) for item in document.get("limitations") or []]
    if limitations:
        for item in limitations:
            canvas.bullet(item)
    else:
        canvas.muted("No additional limitations were recorded.")
    if _truncated(document):
        canvas.gap(6)
        canvas.paragraph("Detailed data was truncated in this human-readable report. Use the JSON or evidence package for the bounded machine-readable details.")

    canvas.section("Reproducibility")
    identity = document.get("identity") or {}
    repro = document.get("reproducibility") or {}
    canvas.kv("Report", document.get("report_id"))
    canvas.kv("Graph version", repro.get("graph_version"))
    canvas.kv("Generated", repro.get("generated_at") or generated)
    if identity.get("service"):
        canvas.kv("Service", identity.get("service"))
    if identity.get("repository_sha"):
        canvas.kv("Repository revision", identity.get("repository_sha"))
    canvas.kv("Fingerprint", repro.get("report_hash"))
    canvas.gap(4)
    canvas.muted("Integrity fingerprint; not a cryptographic signature.")
    return _document(canvas.finish())


def _attention(canvas: _Canvas, document: dict[str, Any]) -> None:
    items = (document.get("recommendations") or [])[:5]
    if not items:
        return
    canvas.section("What needs attention")
    for item in items:
        canvas.bullet(f"{item.get('title')}. {item.get('reason')}")


def _cluster(canvas: _Canvas, document: dict[str, Any]) -> None:
    workspace = document.get("workspace") or {}
    canvas.section("Workspace")
    canvas.kv("Services", workspace.get("service_count"))
    canvas.kv("Repositories", workspace.get("repository_count"))
    canvas.kv("Cross-service relationships", workspace.get("relationship_count"))
    canvas.kv("Contracts", workspace.get("contract_count"))
    canvas.kv("Out of date", workspace.get("graph_stale"))
    canvas.kv("Unresolved references", workspace.get("unresolved_count"))

    canvas.section("Service catalogue")
    services = document.get("services") or []
    shown, note = _cap(services)
    if shown:
        canvas.table(
            ["Service", "Graph", "History", "Health", "Downstream"],
            [160, 90, 90, 90, 86],
            [
                [
                    str(item.get("service") or ""),
                    str(item.get("graph_label") or ""),
                    str(item.get("history_label") or ""),
                    str(item.get("health_label") or ""),
                    _count_text(item.get("downstream")),
                ]
                for item in shown
            ],
        )
        if note:
            canvas.muted(note)
    else:
        canvas.muted("No services are in the published snapshot.")

    canvas.section("Cross-service relationships")
    relation = document.get("relationships") or {}
    rows = relation.get("items") or []
    shown, note = _cap(rows)
    if shown:
        canvas.table(
            ["Consumer", "Contract", "Provider", "Provenance"],
            [120, 176, 120, 100],
            [
                [
                    str(item.get("from_service") or ""),
                    str(item.get("contract") or ""),
                    str(item.get("to_service") or ""),
                    str(item.get("provenance") or ""),
                ]
                for item in shown
            ],
        )
        if note or relation.get("truncated"):
            canvas.muted(note or "Relationship rows were truncated.")
    else:
        canvas.muted("No cross-service relationships are recorded.")
    provenance = relation.get("provenance") or {}
    if provenance:
        canvas.section("Relationship provenance")
        for key, count in provenance.items():
            canvas.kv(str(key), count)
        canvas.muted("Provenance counts come from stored relationship records.")

    canvas.section("Contracts")
    table = (document.get("contracts") or {}).get("table") or []
    shown, note = _cap(table)
    if shown:
        canvas.table(
            ["Provider", "Contract", "Consumers"],
            [140, 196, 180],
            [
                [
                    str(item.get("provider") or ""),
                    str(item.get("contract") or ""),
                    ", ".join(item.get("consumers") or []) or "-",
                ]
                for item in shown
            ],
        )
        if note:
            canvas.muted(note)
    else:
        canvas.muted("No cross-service contracts are recorded.")

    canvas.section("Dependency concentration")
    concentration = document.get("concentration") or {}
    canvas.paragraph(str(concentration.get("note") or "Dependency concentration describes confirmed reach. It is not a risk score."))
    _reach(canvas, "Widest downstream reach", concentration.get("widest_downstream"), "service", "count")
    _reach(canvas, "Most upstream dependencies", concentration.get("most_upstream"), "service", "count")
    widest = concentration.get("widest_contract")
    if widest:
        canvas.kv("Contract with the most consumers", f"{widest.get('contract')} ({widest.get('consumers')})")
    _common_tail(canvas, document)


def _repository(canvas: _Canvas, document: dict[str, Any]) -> None:
    identity = document.get("identity") or {}
    canvas.section("Service")
    canvas.kv("Service", identity.get("service"))
    canvas.kv("Repository", identity.get("repository"))
    canvas.kv("Revision", identity.get("repository_sha"))
    canvas.kv("Graph", identity.get("graph_freshness"))

    architecture = document.get("architecture") or {}
    canvas.section("Architecture")
    canvas.kv("Files", architecture.get("files"))
    canvas.kv("Symbols", architecture.get("symbols"))
    canvas.kv("Internal relationships", architecture.get("internal_relationships"))
    kinds = architecture.get("kinds") or {}
    if kinds:
        canvas.kv("Symbol kinds", ", ".join(f"{name} {count}" for name, count in sorted(kinds.items())))
    for key, title in (("hubs", "Hubs"), ("communities", "Communities"), ("flows", "Flows"), ("bridges", "Bridges")):
        if architecture.get(key) is not None:
            canvas.kv(title, architecture.get(key))

    contracts = document.get("contracts") or {}
    canvas.section("Contracts provided")
    provided = contracts.get("provided") or []
    if provided:
        for item in provided[:TABLE_CAP]:
            consumers = ", ".join(item.get("consumers") or []) or "none recorded"
            canvas.bullet(f"{item.get('name')} ({item.get('kind') or 'contract'}). Consumers: {consumers}")
        if len(provided) > TABLE_CAP:
            canvas.muted(f"Showing {TABLE_CAP} of {len(provided)}.")
    else:
        canvas.muted("No exposed contracts are recorded.")

    canvas.section("Contracts consumed")
    consumed = contracts.get("consumed") or []
    if consumed:
        for item in consumed[:TABLE_CAP]:
            canvas.bullet(f"{item.get('name')} from {item.get('provider') or 'an unresolved provider'}")
        if len(consumed) > TABLE_CAP:
            canvas.muted(f"Showing {TABLE_CAP} of {len(consumed)}.")
    else:
        canvas.muted("No consumed contracts are recorded.")

    linked = document.get("relationships") or {}
    canvas.section("Upstream dependencies")
    _links(canvas, linked.get("upstream") or [], "to_service", "Depends on")
    canvas.section("Downstream dependents")
    _links(canvas, linked.get("downstream") or [], "from_service", "Used by")

    history = document.get("history") or {}
    canvas.section("History")
    canvas.status_line("History", str(history.get("label") or "Unavailable"), str(history.get("status") or ""))
    if history.get("commit_count"):
        canvas.kv("Commits", history.get("commit_count"))
    canvas.paragraph(str(history.get("note") or "Historical change association. This is not a structural dependency."))

    _health(canvas, document.get("health") or {})
    _reviews(canvas, document.get("review_activity") or {})
    _observations(canvas, document)


def _common_tail(canvas: _Canvas, document: dict[str, Any]) -> None:
    history = document.get("history") or {}
    canvas.section("History")
    canvas.status_line("History", str(history.get("label") or "Unavailable"), str(history.get("status") or ""))
    missing = history.get("missing") or []
    if missing:
        canvas.paragraph(f"{len(missing)} repositories have no usable history.")
    canvas.paragraph(str(history.get("note") or "Historical change association. This is not a structural dependency."))
    _health(canvas, document.get("health") or {})
    _reviews(canvas, document.get("review_activity") or {})
    _observations(canvas, document)


def _health(canvas: _Canvas, health: dict[str, Any]) -> None:
    canvas.section("Health")
    for key, title in (
        ("packages", "Package inventory"),
        ("updates", "Dependency hygiene"),
        ("security", "Security advisories"),
        ("coverage", "Coverage"),
    ):
        item = health.get(key) or {}
        canvas.status_line(title, str(item.get("label") or "Unavailable"), str(item.get("status") or ""))
        if item.get("detail"):
            canvas.muted(str(item.get("detail")))


def _reviews(canvas: _Canvas, activity: dict[str, Any]) -> None:
    canvas.section("Recent reviews")
    counts = activity.get("counts") or {}
    canvas.kv("Pass", counts.get("pass", 0))
    canvas.kv("Warn", counts.get("warn", 0))
    canvas.kv("Block", counts.get("block", 0))
    canvas.kv("Incomplete", counts.get("incomplete", 0))
    canvas.muted(str(activity.get("note") or "Review counts describe Nami Gate outcomes and are not a software-quality score."))
    reviews = activity.get("reviews") or []
    if not reviews:
        canvas.muted("No stored reviews matched this report.")
        return
    for item in reviews:
        decision = str(item.get("decision") or "unknown").upper()
        title = item.get("headline") or item.get("service") or item.get("review_id") or "Review"
        canvas.paragraph(f"{decision}. {title}")
        if item.get("what_happened"):
            canvas.muted(str(item["what_happened"]))
        if item.get("why_it_matters"):
            canvas.muted(f"Why it matters: {item['why_it_matters']}")
        evidence = item.get("evidence") or []
        if evidence:
            for entry in evidence:
                canvas.bullet(f"{entry.get('source') or 'Stored evidence'}: {entry.get('what') or ''}")
        else:
            canvas.muted("No evidence statements were stored with this review.")
        if item.get("validation"):
            canvas.muted(str(item["validation"]))
        if item.get("next_action"):
            canvas.muted(f"Next: {item['next_action']}")


def _observations(canvas: _Canvas, document: dict[str, Any]) -> None:
    canvas.section("Key observations")
    inferences = document.get("inferences") or []
    if inferences:
        for item in inferences:
            canvas.bullet(str(item.get("statement") or ""))
    else:
        canvas.muted("No additional observations were recorded.")
    gaps = document.get("intelligence_gaps") or []
    if gaps:
        canvas.section("Intelligence gaps")
        for item in gaps:
            canvas.bullet(str(item))
    canvas.section("Recommendations")
    recommendations = document.get("recommendations") or []
    if recommendations:
        for item in recommendations:
            canvas.bullet(f"{item.get('title')}. {item.get('reason')}")
    else:
        canvas.muted("No recommendations were recorded.")


def _links(canvas: _Canvas, rows: list[dict[str, Any]], field: str, _verb: str) -> None:
    shown, note = _cap(rows)
    if not shown:
        canvas.muted("None recorded.")
        return
    for item in shown:
        canvas.bullet(f"{item.get(field) or '-'} via {item.get('contract') or '-'}")
    if note:
        canvas.muted(note)


def _reach(canvas: _Canvas, label: str, item: dict[str, Any] | None, name_key: str, count_key: str) -> None:
    if not item:
        canvas.kv(label, "-")
        return
    canvas.kv(label, f"{item.get(name_key)} ({item.get(count_key)})")


def _count_text(value: Any) -> str:
    if value in (None, ""):
        return "-"
    return str(value)


def _cap(rows: list[Any], limit: int = TABLE_CAP) -> tuple[list[Any], str]:
    if len(rows) <= limit:
        return rows, ""
    return rows[:limit], f"Showing {limit} of {len(rows)}. The JSON and evidence package include the bounded detail."


def _truncated(document: dict[str, Any]) -> bool:
    relationships = document.get("relationships") or {}
    return bool(isinstance(relationships, dict) and relationships.get("truncated"))


def _tone(status: str, label: str) -> tuple[float, float, float]:
    key = (status or label or "").lower()
    if any(word in key for word in ("complete", "current", "available")) and "not scanned" not in key and "unavailable" not in key:
        return GREEN
    if any(word in key for word in ("partial", "stale", "out of date")):
        return AMBER
    if any(word in key for word in ("limited", "failed")):
        return RED
    return MUTED


class _Canvas:
    def __init__(self) -> None:
        self.pages: list[list[str]] = []
        self.ops: list[str] = []
        self.page_no = 0
        self.y = 0.0
        self._start_page()

    def finish(self) -> list[list[str]]:
        self.pages.append(self.ops)
        return self.pages

    def title(self, text: str) -> None:
        self._ensure(26)
        self._text(text, MARGIN, self.y, 18, color=INK)
        self.y -= 22

    def section(self, text: str) -> None:
        self.y -= 8
        self._ensure(24)
        self._text(text, MARGIN, self.y, 12, bold=True, color=INK)
        self.y -= 5
        self._line(MARGIN, self.y, PAGE_W - MARGIN, self.y, LINE)
        self.y -= 14

    def paragraph(self, text: str) -> None:
        for line in _wrap(text, 10, False, CONTENT_W):
            self._ensure(14)
            self._text(line, MARGIN, self.y, 10, color=INK)
            self.y -= 13
        self.y -= 2

    def muted(self, text: str) -> None:
        for line in _wrap(text, 9, False, CONTENT_W):
            self._ensure(12)
            self._text(line, MARGIN, self.y, 9, color=MUTED)
            self.y -= 12

    def bullet(self, text: str) -> None:
        lines = _wrap(text, 10, False, CONTENT_W - 14)
        for index, line in enumerate(lines):
            self._ensure(13)
            prefix = "- " if index == 0 else "  "
            self._text(prefix + line, MARGIN, self.y, 10, color=INK)
            self.y -= 13

    def kv(self, label: str, value: Any) -> None:
        text = "-" if value in (None, "") else str(value)
        lines = _wrap(text, 10, False, CONTENT_W - 168)
        self._ensure(14 * max(1, len(lines)))
        self._text(label, MARGIN, self.y, 9, color=MUTED)
        for line in lines:
            self._text(line, MARGIN + 168, self.y, 10, color=INK)
            self.y -= 14

    def status_line(self, label: str, value: str, status: str) -> None:
        self._ensure(14)
        self._text(label, MARGIN, self.y, 10, color=INK)
        shown = value or "Unavailable"
        self._text(shown, MARGIN + 168, self.y, 10, bold=True, color=_tone(status, shown))
        self.y -= 15

    def gap(self, amount: float) -> None:
        self.y -= amount

    def table(self, headers: list[str], widths: list[float], rows: list[list[str]]) -> None:
        self._table_header(headers, widths)
        for row in rows:
            if self._ensure(14):
                self._table_header(headers, widths)
            x = MARGIN
            for cell, width in zip(row, widths):
                self._text(_fit(cell, 8, False, width - 8), x, self.y, 8, color=INK)
                x += width
            self.y -= 4
            self._line(MARGIN, self.y, PAGE_W - MARGIN, self.y, LINE)
            self.y -= 11

    def _table_header(self, headers: list[str], widths: list[float]) -> None:
        self._ensure(16)
        x = MARGIN
        for header, width in zip(headers, widths):
            self._text(_fit(header, 8, True, width - 8), x, self.y, 8, bold=True, color=MUTED)
            x += width
        self.y -= 4
        self._line(MARGIN, self.y, PAGE_W - MARGIN, self.y, LINE)
        self.y -= 12

    def _start_page(self) -> None:
        if self.page_no:
            self.pages.append(self.ops)
        self.page_no += 1
        self.ops = []
        self._rect(0, 0, PAGE_W, PAGE_H, WHITE)
        self._text("Nami Trace", MARGIN, 760, 9, bold=True, color=BLUE)
        brand = "Intelligence report"
        self._text(brand, PAGE_W - MARGIN - _width(brand, 9, False), 760, 9, color=MUTED)
        self._line(MARGIN, 748, PAGE_W - MARGIN, 748, BLUE)
        self._line(MARGIN, 40, PAGE_W - MARGIN, 40, LINE)
        page = str(self.page_no)
        self._text(page, PAGE_W - MARGIN - _width(page, 8, False), 26, 8, color=MUTED)
        self._text("Nami Trace", MARGIN, 26, 8, color=MUTED)
        self.y = 728

    def _ensure(self, height: float) -> bool:
        if self.y - height >= 52:
            return False
        self._start_page()
        return True

    def _text(self, value: str, x: float, y: float, size: float, bold: bool = False, color: tuple[float, float, float] = INK) -> None:
        font = "/F2" if bold else "/F1"
        red, green, blue = color
        self.ops.append(
            f"BT {red:.3f} {green:.3f} {blue:.3f} rg {font} {size:.1f} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm ({_escape(_latin(value))}) Tj ET"
        )

    def _line(self, x1: float, y: float, x2: float, _y2: float, color: tuple[float, float, float]) -> None:
        red, green, blue = color
        self.ops.append(f"{red:.3f} {green:.3f} {blue:.3f} RG 0.6 w {x1:.2f} {y:.2f} m {x2:.2f} {y:.2f} l S")

    def _rect(self, x: float, y: float, width: float, height: float, color: tuple[float, float, float]) -> None:
        red, green, blue = color
        self.ops.append(f"{red:.3f} {green:.3f} {blue:.3f} rg {x:.2f} {y:.2f} {width:.2f} {height:.2f} re f")


def _wrap(text: str, size: float, bold: bool, max_width: float) -> list[str]:
    words = str(text or "").split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if _width(trial, size, bold) <= max_width:
            current = trial
            continue
        if current:
            lines.append(current)
        current = _hard(word, size, bold, max_width, lines)
    if current:
        lines.append(current)
    return lines


def _hard(word: str, size: float, bold: bool, max_width: float, lines: list[str]) -> str:
    if _width(word, size, bold) <= max_width:
        return word
    chunk = ""
    for char in word:
        if _width(chunk + char, size, bold) <= max_width:
            chunk += char
        else:
            if chunk:
                lines.append(chunk)
            chunk = char
    return chunk


def _fit(text: str, size: float, bold: bool, max_width: float) -> str:
    value = str(text or "")
    if _width(value, size, bold) <= max_width:
        return value
    while value and _width(value + "...", size, bold) > max_width:
        value = value[:-1]
    return f"{value}..." if value else ""


def _width(text: str, size: float, bold: bool) -> float:
    return len(text) * size * (0.52 if bold else 0.48)


def _latin(value: str) -> str:
    return value.replace("\t", " ").replace("\r", "").replace("\n", " ").encode("latin-1", errors="replace").decode("latin-1")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _document(pages: list[list[str]]) -> bytes:
    streams = ["\n".join(page).encode("latin-1") for page in pages]
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    ]
    kids = []
    for index, stream in enumerate(streams):
        page_number = 5 + index * 2
        kids.append(f"{page_number} 0 R")
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                f"/Contents {page_number + 1} 0 R /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>"
            ).encode("ascii")
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Count {len(pages)} /Kids [{' '.join(kids)}] >>".encode("ascii")
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
