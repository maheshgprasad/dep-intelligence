import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { getIntelligenceReport } from "../api";
import { ReportDetailPage } from "./ReportDetailPage";

vi.mock("../api", () => ({
  getIntelligenceReport: vi.fn(),
}));

function report(overrides: Record<string, unknown> = {}) {
  return {
    report_id: "rpt-fixture",
    scope: "service_cluster",
    generated_at: "2026-01-01T00:00:00Z",
    identity: { cluster_label: "AUTH service cluster", cluster_id: "svc-cluster-fixture", services: ["auth-service", "user-service"] },
    executive_summary: { headline: "AUTH service cluster", summary: "Fixture.", readiness_label: "Partial" },
    intelligence_status: {},
    inferences: [],
    recommendations: [],
    limitations: [],
    reproducibility: { graph_version: "fixture", report_hash: "sha", note: "Integrity fingerprint." },
    workspace: { service_count: 2, relationship_count: 1, contract_count: 1, graph_stale: 0 },
    provider_discovery: { status: "not_scanned" },
    review_activity: {
      summary_version: 2,
      counts: { pass: 0, warn: 1, block: 0, incomplete: 0 },
      note: "Counts include all stored reviews that matched this report scope at generation time.",
      attention_reviews: [{
        review_id: "rev-1",
        decision: "warn",
        headline: "Historical association is not a blocker",
        service: "auth-service",
        primary_rule: { rule_id: "BG-TEMPORAL-001", title: "Historical association is not a blocker" },
        targets: { unique_file_count: 1, total_findings: 1, items: [{ file: "src/app.py" }] },
        why_it_matters: "Historical association is not a blocker.",
        evidence: [{ source: "Repository history", statement: "Repository history identified src/app.py." }],
        ranking_context: { source: "Nami Rank", statement: "Nami Rank prioritized this target." },
        cross_cluster_targets: ["billing-service"],
        validation_status: "failed",
        validation: "Decision logic validated",
        next_action: "Review src/app.py before deployment.",
      }],
    },
    ...overrides,
  };
}

describe("Report detail", () => {
  it("shows an attention review with external targets and ranking context", async () => {
    vi.mocked(getIntelligenceReport).mockResolvedValue(report() as never);
    render(
      <MemoryRouter initialEntries={["/reports/rpt-fixture"]}>
        <Routes><Route path="/reports/:reportId" element={<ReportDetailPage />} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText(/WARN · Historical association is not a blocker/)).toBeInTheDocument();
    expect(screen.getByText(/BG-TEMPORAL-001/)).toBeInTheDocument();
    expect(screen.getAllByText(/src\/app.py/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Evidence: Repository history/)).toBeInTheDocument();
    expect(screen.getByText(/Ranking context: Nami Rank/)).toBeInTheDocument();
    expect(screen.getByText(/External review targets: billing-service/)).toBeInTheDocument();
    expect(screen.getByText(/outside the selected Service Cluster/)).toBeInTheDocument();
    expect(screen.getByText("Decision logic: Validation failed")).toBeInTheDocument();
    expect(screen.getByText(/Review src\/app.py before deployment/)).toBeInTheDocument();
    expect(screen.queryByText(/validated evidence/i)).not.toBeInTheDocument();
  });

  it("notes a report stored before enriched summaries", async () => {
    vi.mocked(getIntelligenceReport).mockResolvedValue(report({
      review_activity: { counts: { pass: 0, warn: 0, block: 0, incomplete: 0 }, attention_reviews: [] },
    }) as never);
    render(
      <MemoryRouter initialEntries={["/reports/rpt-fixture"]}>
        <Routes><Route path="/reports/:reportId" element={<ReportDetailPage />} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText(/before enriched review summaries were stored/)).toBeInTheDocument();
  });

  it("shows not scanned separately from zero provider references", async () => {
    vi.mocked(getIntelligenceReport).mockResolvedValue(report({ provider_discovery: { status: "not_scanned" } }) as never);
    const { unmount } = render(
      <MemoryRouter initialEntries={["/reports/rpt-fixture"]}>
        <Routes><Route path="/reports/:reportId" element={<ReportDetailPage />} /></Routes>
      </MemoryRouter>,
    );
    expect((await screen.findAllByText(/Topology provider discovery: Not scanned/)).length).toBeGreaterThan(0);
    unmount();
    vi.mocked(getIntelligenceReport).mockResolvedValue(report({ provider_discovery: { status: "complete", unique_unresolved_reference_count: 0 } }) as never);
    render(
      <MemoryRouter initialEntries={["/reports/rpt-fixture"]}>
        <Routes><Route path="/reports/:reportId" element={<ReportDetailPage />} /></Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByText("0 unique unresolved provider references.")).toBeInTheDocument();
  });
});
