import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import type { ClusterReviewState } from "../clusterReview";
import { ClusterReviewProgress, ClusterReviewRecord } from "./ClusterReviewPage";

const review: ClusterReviewState = {
  cluster_review_id: "cluster-1",
  cluster_id: "omnichannel",
  cluster_label: "Omnichannel",
  status: "running",
  sequence: 8,
  repository_total: 5,
  percent: 40,
  counters: {
    repository_total: 5,
    repositories_inspected: 2,
    repositories_pending: 2,
    repositories_running: 1,
    repositories_with_changes: 2,
    repositories_no_change: 1,
    reviews_completed: 1,
    reviews_failed: 0,
  },
  running: [],
  review_stages: [
    { stage: "build_change_view", label: "Building change view" },
    { stage: "align_graph", label: "Aligning graph evidence" },
    { stage: "calculate_impact", label: "Calculating cross-repository impact" },
    { stage: "gate_decision", label: "Evaluating Boundary Gate" },
  ],
  assessment: null,
  repositories: [
    { service_id: "uocp-analytics-service", repository: "uocp-analytics-service", repository_index: 1, status: "no_change", stage: "", stage_label: "", stage_index: 0, stage_count: 9, change_detected: false, changed_file_count: 0, review_id: "", decision: "", validation_status: "", error_code: "", error_summary: "" },
    { service_id: "uocp-api-gateway", repository: "uocp-api-gateway", repository_index: 2, status: "completed", stage: "", stage_label: "Complete", stage_index: 9, stage_count: 9, change_detected: true, changed_file_count: 3, review_id: "review-gateway", decision: "warn", validation_status: "validated", error_code: "", error_summary: "" },
    { service_id: "uocp-auth-service", repository: "uocp-auth-service", repository_index: 3, status: "reviewing", stage: "calculate_impact", stage_label: "Calculating cross-repository impact", stage_index: 4, stage_count: 9, change_detected: true, changed_file_count: 4, review_id: "review-auth", decision: "", validation_status: "", error_code: "", error_summary: "" },
    { service_id: "uocp-audit-service", repository: "uocp-audit-service", repository_index: 4, status: "pending", stage: "", stage_label: "", stage_index: 0, stage_count: 9, change_detected: false, changed_file_count: 0, review_id: "", decision: "", validation_status: "", error_code: "", error_summary: "" },
    { service_id: "uocp-routing-service", repository: "uocp-routing-service", repository_index: 5, status: "failed", stage: "", stage_label: "", stage_index: 0, stage_count: 9, change_detected: true, changed_file_count: 1, review_id: "", decision: "", validation_status: "", error_code: "review_failed", error_summary: "graph unavailable" },
  ],
};

describe("cluster review progress", () => {
  afterEach(() => cleanup());

  it("shows the active repository, stage, and outcomes without treating no change as pass", () => {
    render(<MemoryRouter><ClusterReviewProgress review={{ ...review, running: [review.repositories[2]] }} /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "Reviewing Omnichannel cluster" })).toBeInTheDocument();
    expect(screen.getByText("2 / 5 repositories inspected")).toBeInTheDocument();
    expect(screen.getByText("Changes detected 2")).toBeInTheDocument();
    expect(screen.getByText("No changes 1")).toBeInTheDocument();
    expect(screen.getByText("Currently running 1")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Currently analysing" })).toBeInTheDocument();
    expect(screen.getAllByText("uocp-auth-service").length).toBeGreaterThan(0);
    expect(screen.getByText("Calculating cross-repository impact Current")).toBeInTheDocument();
    expect(screen.getByText("Building change view Complete")).toBeInTheDocument();
    expect(screen.getByText("Evaluating Boundary Gate Waiting")).toBeInTheDocument();
    expect(screen.getAllByText("No changes").length).toBeGreaterThan(0);
    expect(screen.getAllByText("4 changed files · Reviewing · Calculating cross-repository impact").length).toBeGreaterThan(0);
    expect(screen.getByText("3 changed files · WARN · Validated")).toBeInTheDocument();
    expect(screen.getByText("Failed · graph unavailable")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "View repository review" }).some((item) => item.getAttribute("href") === "/review/review-gateway")).toBe(true);
    expect(screen.queryByText("PASS")).not.toBeInTheDocument();
  });

  it("shows a completed cluster assessment and a repository review link", () => {
    const completed: ClusterReviewState = {
      ...review,
      status: "completed",
      percent: 100,
      running: [],
      counters: { ...review.counters, repositories_inspected: 5, repositories_pending: 0, repositories_running: 0, repositories_with_changes: 2, repositories_no_change: 3, reviews_completed: 2, reviews_failed: 0 },
      repositories: review.repositories.map((item) => item.service_id === "uocp-auth-service"
        ? { ...item, status: "completed", decision: "pass", validation_status: "validated", review_id: "review-auth", stage_label: "Complete" }
        : item.service_id === "uocp-routing-service"
          ? { ...item, status: "no_change", change_detected: false, changed_file_count: 0, error_summary: "" }
          : item),
      assessment: {
        code: "attention_required",
        label: "ATTENTION REQUIRED",
        summary: "1 warning, 1 pass",
        verified_blockers: 0,
        warnings: 1,
        passes: 1,
        incomplete: 0,
        failed: 0,
        repositories_with_changes: 2,
        repositories_no_change: 3,
        evidenced_service_count: 2,
        evidenced_services: ["uocp-api-gateway", "uocp-auth-service"],
        complete: true,
      },
    };
    render(<MemoryRouter><ClusterReviewProgress review={completed} /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "Omnichannel cluster review complete" })).toBeInTheDocument();
    expect(screen.getByText("ATTENTION REQUIRED")).toBeInTheDocument();
    expect(screen.getByText("2 / 5 services have evidenced impact")).toBeInTheDocument();
    expect(screen.getByText("1 warning, 1 pass")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View cluster review" })).toHaveAttribute("href", "/review/cluster/cluster-1/detail");
    const links = screen.getAllByRole("link", { name: "View repository review" });
    expect(links.map((item) => item.getAttribute("href"))).toEqual(expect.arrayContaining(["/review/review-gateway", "/review/review-auth"]));
  });

  it("opens the stored cluster review instead of the progress page", () => {
    const completed: ClusterReviewState = {
      ...review,
      status: "completed",
      assessment: {
        code: "no_changes",
        label: "NO CHANGES",
        summary: "",
        verified_blockers: 0,
        warnings: 0,
        passes: 0,
        incomplete: 0,
        failed: 0,
        repositories_with_changes: 0,
        repositories_no_change: 5,
        evidenced_service_count: 0,
        evidenced_services: [],
        complete: true,
      },
    };
    render(<MemoryRouter><ClusterReviewRecord review={completed} /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "Omnichannel cluster review" })).toBeInTheDocument();
    expect(screen.getByText("NO CHANGES")).toBeInTheDocument();
    expect(screen.getByText("No service has evidenced impact.")).toBeInTheDocument();
    expect(screen.getAllByText("No changes").length).toBeGreaterThan(0);
    expect(screen.queryByText("PASS")).not.toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "View repository review" }).some((item) => item.getAttribute("href") === "/review/review-gateway")).toBe(true);
  });

  it("shows the relationship conclusion and the same export menu as a review", () => {
    const completed: ClusterReviewState = {
      ...review,
      status: "completed",
      copy_summary: "Omnichannel: ATTENTION REQUIRED. 1 broken.",
      assessment: {
        code: "attention_required",
        label: "ATTENTION REQUIRED",
        summary: "1 unresolved contract break.",
        verified_blockers: 1,
        warnings: 0,
        passes: 0,
        incomplete: 0,
        failed: 0,
        repositories_with_changes: 1,
        repositories_no_change: 4,
        evidenced_service_count: 1,
        evidenced_services: ["uocp-api-gateway"],
        complete: true,
      },
      interdependencies: {
        comparison_mode: "evidence_derived",
        summary: { examined: 2, broken: 1, potentially_broken: 0, modified: 0, introduced: 0, relationship_removed: 0, coordinated_change: 0, resolved_by_change_set: 0, unchanged: 1 },
        unchanged_but_affected_services: ["uocp-api-gateway"],
        items: [
          {
            source_service: "uocp-auth-service",
            target_service: "uocp-api-gateway",
            relationship: "http_contract",
            contract_id: "POST /v1/validate",
            classification: "broken",
            statement: "uocp-auth-service removed POST /v1/validate. uocp-api-gateway still uses it.",
            required_action: "Migrate the consumer or restore the contract.",
            evidence_ids: ["EV-021"],
            child_review_ids: ["review-auth"],
            path: [{ service_id: "uocp-auth-service" }, { service_id: "uocp-api-gateway" }],
          },
          {
            source_service: "uocp-audit-service",
            target_service: "uocp-routing-service",
            relationship: "http_contract",
            contract_id: "GET /v1/health",
            classification: "unchanged",
            statement: "Unchanged.",
          },
        ],
      },
    };
    render(<MemoryRouter><ClusterReviewRecord review={completed} /></MemoryRouter>);
    expect(screen.getByRole("heading", { name: "Interdependency changes" })).toBeInTheDocument();
    expect(screen.getByText("Broken 1")).toBeInTheDocument();
    expect(screen.getByText("uocp-auth-service removed POST /v1/validate. uocp-api-gateway still uses it.")).toBeInTheDocument();
    expect(screen.getByText("Unchanged but affected: uocp-api-gateway")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeInTheDocument();
    expect(screen.getByText("1 relationship unchanged")).toBeInTheDocument();
    expect(screen.queryByText("GET /v1/health")).not.toBeInTheDocument();
  });
});
