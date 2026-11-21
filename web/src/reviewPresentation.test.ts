import { describe, expect, it } from "vitest";
import type { ReviewReport } from "./api";
import { orderedFindings, primaryTarget, serviceGroups } from "./reviewPresentation";

function report(): ReviewReport {
  return {
    review_id: "r",
    gate: { decision: "warn", reason_codes: [], blocking_findings: ["block"], warning_findings: [], review_targets: [], evidence_complete: true },
    change: { service_id: "auth" },
    summary: { text: "WARN" },
    changed_files: [],
    changed_symbols: [],
    contract_changes: [],
    impact: {
      affected_services: ["gateway", "routing", "quiet-history"],
      cluster_downstream: { items: [{ service_id: "routing", direct: true }] },
      ranked_candidates: [
        { service_id: "gateway", file_path: "common/runtime.py", evidence_origin: "structural_only", hybrid_score: 0.9 },
        { service_id: "gateway", file_path: "app.py", evidence_origin: "structural_only", hybrid_score: 0.4 },
        { service_id: "routing", file_path: "common/runtime.py", evidence_origin: "both", hybrid_score: 0.2 },
        { service_id: "quiet-history", file_path: "notes.py", evidence_origin: "temporal_only", hybrid_score: 0.8 },
      ],
    },
    findings: [
      { finding_id: "time", category: "review_target", severity: "info", title: "history", claim: "history", evidence_strength: "weak", service_id: "quiet-history", structured: { evidence_origin: "temporal_only" } },
      { finding_id: "block", category: "contract_break", severity: "high", title: "break", claim: "break", evidence_strength: "verified", service_id: "gateway" },
      { finding_id: "path", category: "cross_repo_impact", severity: "low", title: "path", claim: "path", evidence_strength: "moderate", service_id: "gateway", structured: { evidence_origin: "structural_only" } },
    ],
    verification: [],
    policies: [],
    diagnostics: [],
    limitations: [],
    review_scope: { mode: "service_cluster", origin_service_id: "auth", member_count: 4, affected_member_count: 3, unaffected_member_count: 1, unaffected_member_services: ["audit"] },
  };
}

describe("review presentation", () => {
  it("keeps every service and file while leading with a direct target", () => {
    const groups = serviceGroups(report());
    expect(groups.map((item) => item.serviceId)).toEqual(["routing", "gateway", "quiet-history"]);
    expect(groups.find((item) => item.serviceId === "gateway")?.files).toEqual(["common/runtime.py", "app.py"]);
    expect(primaryTarget(report())).toBe("routing");
  });

  it("orders a verified blocker ahead of temporal context", () => {
    expect(orderedFindings(report()).map((item) => item.finding_id)).toEqual(["block", "path", "time"]);
  });
});
