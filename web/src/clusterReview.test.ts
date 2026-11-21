import { describe, expect, it } from "vitest";
import { applyClusterSnapshot, repositoryDetail, stageState, type ClusterReviewState } from "./clusterReview";

function review(overrides: Partial<ClusterReviewState> = {}): ClusterReviewState {
  return {
    cluster_review_id: "cluster-1",
    cluster_id: "omnichannel",
    cluster_label: "Omnichannel",
    status: "running",
    sequence: 1,
    repository_total: 3,
    percent: 0,
    counters: {
      repository_total: 3,
      repositories_inspected: 0,
      repositories_pending: 3,
      repositories_running: 0,
      repositories_with_changes: 0,
      repositories_no_change: 0,
      reviews_completed: 0,
      reviews_failed: 0,
    },
    repositories: [
      repo("repo-a", "pending"),
      repo("repo-b", "pending"),
      repo("repo-c", "pending"),
    ],
    running: [],
    review_stages: [
      { stage: "build_change_view", label: "Building change view" },
      { stage: "calculate_impact", label: "Calculating cross-repository impact" },
    ],
    assessment: null,
    ...overrides,
  };
}

function repo(service: string, status: string, extra: Record<string, unknown> = {}) {
  return {
    service_id: service,
    repository: service,
    repository_index: 1,
    status,
    stage: "",
    stage_label: "",
    stage_index: 0,
    stage_count: 9,
    change_detected: false,
    changed_file_count: 0,
    review_id: "",
    decision: "",
    validation_status: "",
    error_code: "",
    error_summary: "",
    ...extra,
  };
}

describe("cluster review state", () => {
  it("replaces a repository row instead of appending duplicates", () => {
    const first = applyClusterSnapshot(null, review());
    const next = applyClusterSnapshot(first, review({
      sequence: 2,
      repositories: [
        repo("repo-a", "no_change"),
        repo("repo-b", "reviewing", { stage: "calculate_impact", stage_label: "Calculating cross-repository impact", changed_file_count: 4, change_detected: true }),
        repo("repo-c", "pending"),
        repo("repo-b", "reviewing", { stage: "calculate_impact" }),
      ],
    }));
    expect(next.repositories.map((item) => item.service_id)).toEqual(["repo-a", "repo-b", "repo-c"]);
    expect(next.repositories[1].status).toBe("reviewing");
  });

  it("ignores an older snapshot after a newer one", () => {
    const current = applyClusterSnapshot(null, review({
      sequence: 4,
      repositories: [repo("repo-b", "completed", { decision: "warn", review_id: "review-b" }), repo("repo-d", "reviewing")],
    }));
    const late = applyClusterSnapshot(current, review({
      sequence: 3,
      repositories: [repo("repo-b", "reviewing"), repo("repo-d", "pending")],
    }));
    expect(late.repositories[0].status).toBe("completed");
    expect(late.repositories[0].review_id).toBe("review-b");
    expect(late.sequence).toBe(4);
  });

  it("keeps several repositories reviewing at once", () => {
    const state = applyClusterSnapshot(null, review({
      sequence: 5,
      repositories: [
        repo("auth", "reviewing", { stage_label: "Calculating cross-repository impact" }),
        repo("routing", "reviewing", { stage_label: "Building change view" }),
        repo("gateway", "pending"),
      ],
      running: [
        repo("auth", "reviewing"),
        repo("routing", "reviewing"),
      ],
    }));
    expect(state.running).toHaveLength(2);
    expect(state.repositories.filter((item) => item.status === "reviewing")).toHaveLength(2);
  });

  it("describes outcomes without calling no change a pass", () => {
    expect(repositoryDetail(repo("audit", "no_change"))).toBe("No changes");
    expect(repositoryDetail(repo("auth", "completed", { decision: "warn", changed_file_count: 4, validation_status: "validated" }))).toBe("4 changed files · WARN · Validated");
    expect(repositoryDetail(repo("billing", "pending"))).toBe("Waiting");
    expect(repositoryDetail(repo("routing", "failed", { error_summary: "graph unavailable" }))).toBe("Failed · graph unavailable");
    expect(stageState(
      [{ stage: "build_change_view" }, { stage: "calculate_impact" }, { stage: "gate_decision" }],
      "calculate_impact",
      "build_change_view",
    )).toBe("complete");
    expect(stageState(
      [{ stage: "build_change_view" }, { stage: "calculate_impact" }],
      "calculate_impact",
      "calculate_impact",
    )).toBe("current");
  });
});
