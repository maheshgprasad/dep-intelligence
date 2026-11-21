import { describe, expect, it } from "vitest";
import { reviewFormState, scopeColumnLabel } from "./reviewForm";

const base = {
  reviewScope: "service_cluster" as const,
  clusterId: "omnichannel",
  repository: "uocp-api-gateway",
  repositoryLabel: "uocp-api-gateway",
  source: "working_tree" as const,
  commit: "",
  base: "",
  head: "",
  since: "",
  until: "",
  prUrl: "",
  dirtyFiles: 0,
  stagedFiles: 0,
  branch: "main",
  verificationCommands: [] as string[],
  verifyRequested: true,
  mode: "auto" as const,
};

describe("review form readiness", () => {
  it("blocks an empty working tree for one repository", () => {
    const state = reviewFormState({ ...base, reviewScope: "repository" });
    expect(state.canPreview).toBe(false);
    expect(state.canReview).toBe(false);
    expect(state.sourceMessage).toBe("No working-tree changes found in uocp-api-gateway.");
  });

  it("lets a cluster review inspect every repository when the origin is clean", () => {
    const state = reviewFormState(base);
    expect(state.canPreview).toBe(true);
    expect(state.canReview).toBe(true);
    expect(state.showClusterContext).toBe(true);
    expect(state.sourceMessage).toBe("Preview lists every repository in the cluster before review starts.");
  });

  it("blocks an empty staged change for one repository", () => {
    const state = reviewFormState({ ...base, reviewScope: "repository", source: "staged", dirtyFiles: 3 });
    expect(state.canPreview).toBe(false);
    expect(state.sourceMessage).toBe("No staged changes found in uocp-api-gateway.");
  });

  it("enables a working tree that has changes", () => {
    const state = reviewFormState({ ...base, reviewScope: "repository", dirtyFiles: 3 });
    expect(state.canPreview).toBe(true);
    expect(state.canReview).toBe(true);
    expect(state.showClusterContext).toBe(false);
    expect(state.sourceMessage).toBe("3 changed files on main");
  });

  it("keeps verify false when no commands are configured", () => {
    const state = reviewFormState({ ...base, dirtyFiles: 2, verifyRequested: true });
    expect(state.verificationEnabled).toBe(false);
    expect(state.verify).toBe(false);
  });

  it("sends verify when commands exist and the toggle is on", () => {
    const state = reviewFormState({ ...base, dirtyFiles: 2, verificationCommands: ["pytest"], verifyRequested: true });
    expect(state.verificationEnabled).toBe(true);
    expect(state.verify).toBe(true);
  });

  it("shows only the selected review mode", () => {
    expect(reviewFormState({ ...base, mode: "auto" }).modeText).toContain("private graph evidence");
    expect(reviewFormState({ ...base, mode: "fast" }).modeText).toBe("Uses the published intelligence snapshot.");
    expect(reviewFormState({ ...base, mode: "exact" }).modeText).toContain("does not publish it");
  });

  it("requires commit, range, and pull request fields", () => {
    expect(reviewFormState({ ...base, source: "commit" }).canReview).toBe(false);
    expect(reviewFormState({ ...base, source: "commit", commit: "abc" }).canReview).toBe(true);
    expect(reviewFormState({ ...base, source: "range", base: "main" }).canReview).toBe(false);
    expect(reviewFormState({ ...base, source: "range", base: "main", head: "topic" }).canReview).toBe(true);
    expect(reviewFormState({ ...base, source: "github_pr" }).canReview).toBe(false);
    expect(reviewFormState({ ...base, source: "github_pr", prUrl: "https://github.com/acme/repo/pull/1" }).canReview).toBe(true);
    expect(reviewFormState({ ...base, reviewScope: "repository", source: "timeframe", dirtyFiles: 0 }).canReview).toBe(false);
    expect(reviewFormState({ ...base, reviewScope: "repository", source: "timeframe", since: "2024-02-01", until: "2024-01-01" }).canReview).toBe(false);
    expect(reviewFormState({ ...base, reviewScope: "repository", source: "timeframe", since: "2024-01-01", until: "2024-01-31" }).canReview).toBe(true);
  });

  it("labels recent review scope with the cluster name", () => {
    expect(scopeColumnLabel("service_cluster", "Omnichannel")).toBe("Service cluster · Omnichannel");
    expect(scopeColumnLabel("repository", "Repository")).toBe("Individual repository");
  });
});
