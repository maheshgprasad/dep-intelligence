"""Product surfaces stay registered. A backend-only feature is a regression."""

from __future__ import annotations

from pathlib import Path

from dep_intel.api import app


ROOT = Path(__file__).resolve().parents[2]


def test_ui_routes_stay_registered() -> None:
    text = (ROOT / "web/src/App.tsx").read_text(encoding="utf-8")
    for route in ('path="/"', 'path="/review"', 'path="/review/:reviewId"', 'path="/impact"', 'path="/architecture"', 'path="/reports"', 'path="/reports/:reportId"', 'path="/health"', 'path="/research"'):
        assert route in text
    nav = text.split("HeaderNavigation", 1)[1].split("HeaderGlobalBar", 1)[0]
    assert 'href="/reports"' in nav
    assert nav.index('href="/reports"') < nav.index('menuLinkName="More"')


def test_failed_validation_copy_does_not_claim_validated_evidence() -> None:
    text = (ROOT / "web/src/components/NamiCommentary.tsx").read_text(encoding="utf-8")
    assert 'if (status === "failed") return "Generated from stored Nami evidence; decision validation failed"' in text
    assert 'if (status === "validated") return "Generated from stored Nami evidence"' in text


def test_api_families_stay_registered() -> None:
    paths = {getattr(route, "path", "") for route in app.routes}
    assert any(path.startswith("/api/review") for path in paths)
    assert any(path.startswith("/api/reports") for path in paths)
    assert any(path.startswith("/api/cluster") for path in paths)
    assert any(path.startswith("/api/repository-health") or path.startswith("/api/health") for path in paths)
    assert "/api/topology" in paths
    assert "/api/research/gate-validation" in paths
