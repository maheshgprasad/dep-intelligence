import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getServiceClusters, previewChange, previewClusterReview, recentReviews, reviewConfig, reviewSourceOptions, runClusterReview, runReview, type AppState } from "../api";
import { ReviewPage } from "./ReviewPage";

vi.mock("../api", () => ({
  getServiceClusters: vi.fn(),
  reviewConfig: vi.fn(),
  reviewSourceOptions: vi.fn(),
  recentReviews: vi.fn(),
  previewChange: vi.fn(),
  previewClusterReview: vi.fn(),
  runReview: vi.fn(),
  runClusterReview: vi.fn(),
  purgeReviews: vi.fn(),
}));

const snapshot = {
  repositories: [
    { slug: "uocp-api-gateway", name: "API gateway", service_id: "uocp-api-gateway", checkout_status: "available" },
    { slug: "uocp-auth-service", name: "Auth", service_id: "uocp-auth-service", checkout_status: "available" },
    { slug: "fastapi", name: "FastAPI sample", service_id: "fastapi", checkout_status: "available" },
  ],
} as AppState;

const cluster = {
  cluster_id: "omnichannel",
  label: "omnichannel",
  display_label: "Omnichannel",
  service_count: 18,
  relationship_count: 4,
  services: ["uocp-api-gateway", "uocp-auth-service"],
  membership_source: "declared",
  membership_source_label: "Declared in repos.txt",
};

describe("Review change page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getServiceClusters).mockResolvedValue({ clusters: [cluster], standalone: [] });
    vi.mocked(reviewConfig).mockResolvedValue({ model_configured: false, policies: [], diagnostics: [], score_note: "", verification: { commands: [] } });
    vi.mocked(reviewSourceOptions).mockResolvedValue({ dirty_files: 0, staged_files: 0, current_branch: "main", default_branch: "main", branches: ["main"], recent_commits: [] });
    vi.mocked(recentReviews).mockResolvedValue([
      { review_id: "cluster-review", decision: "warn", repository: "uocp-auth-service", service: "uocp-auth-service", source_kind: "working_tree", completed_at: "today", finding_count: 8, base_sha: "", head_sha: "", review_scope: "service_cluster", review_scope_label: "Omnichannel" },
      { review_id: "repo-review", decision: "pass", repository: "fastapi", service: "fastapi", source_kind: "working_tree", completed_at: "today", finding_count: 0, base_sha: "", head_sha: "", review_scope: "repository", review_scope_label: "Repository" },
    ]);
  });

  afterEach(() => {
    cleanup();
  });

  it("keeps repository mode and names an empty working tree", async () => {
    render(<MemoryRouter><ReviewPage snapshot={snapshot} /></MemoryRouter>);
    expect(await screen.findByText("No working-tree changes found in API gateway.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview change" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Review change" })).toBeDisabled();
    expect(screen.getByRole("columnheader", { name: "Origin" })).toBeInTheDocument();
    expect(screen.getAllByText("Individual repository").length).toBeGreaterThan(0);
    expect(screen.getByRole("switch", { name: "Run verification" })).toBeDisabled();
    expect(screen.getByText("Uses the published graph when it matches this change, and builds private graph evidence when required.")).toBeInTheDocument();
    expect(screen.queryByText("Uses the published intelligence snapshot.")).not.toBeInTheDocument();
    expect(screen.getByText("No verification commands are configured for API gateway.")).toBeInTheDocument();
  });

  it("shows declared cluster provenance and limits the origin list", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><ReviewPage snapshot={snapshot} /></MemoryRouter>);
    await user.click(await screen.findByRole("combobox", { name: "Review scope" }));
    await user.click(await screen.findByRole("option", { name: "Service cluster" }));
    expect(await screen.findByText("Declared in repos.txt · 18 services")).toBeInTheDocument();
    expect(screen.getByText("Preview lists every repository in the cluster before review starts.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview cluster changes" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Review cluster changes" })).toBeEnabled();
    await user.click(screen.getByRole("combobox", { name: "Change origin repository" }));
    expect(screen.getByRole("option", { name: "uocp-api-gateway" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "uocp-auth-service" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "FastAPI sample" })).not.toBeInTheDocument();
    expect(screen.getByText("Service cluster · Omnichannel")).toBeInTheDocument();
  });

  it("enables verification only when commands exist and does not start an empty review", async () => {
    const user = userEvent.setup();
    vi.mocked(reviewConfig).mockResolvedValue({ model_configured: false, policies: [], diagnostics: [], score_note: "", verification: { commands: ["pytest"] } });
    vi.mocked(reviewSourceOptions).mockResolvedValue({ dirty_files: 2, staged_files: 0, current_branch: "main", default_branch: "main", branches: ["main"], recent_commits: [] });
    render(<MemoryRouter><ReviewPage snapshot={snapshot} /></MemoryRouter>);
    expect(await screen.findByText("Runs pytest.")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Run verification" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Review change" }));
    await waitFor(() => expect(runReview).toHaveBeenCalled());
    expect(vi.mocked(runReview).mock.calls[0][0]).toMatchObject({ verify: true });
    expect(previewChange).not.toHaveBeenCalled();
    expect(runClusterReview).not.toHaveBeenCalled();
  });

  it("previews cluster changes and starts a cluster review without the repository working state", async () => {
    const user = userEvent.setup();
    vi.mocked(previewClusterReview).mockResolvedValue({
      cluster_id: "omnichannel",
      cluster_label: "Omnichannel",
      repository_total: 18,
      changed_count: 2,
      no_change_count: 16,
      repositories: [
        { service_id: "uocp-auth-service", repository: "uocp-auth-service", change_detected: true, changed_file_count: 4 },
        { service_id: "uocp-api-gateway", repository: "uocp-api-gateway", change_detected: true, changed_file_count: 2 },
        { service_id: "uocp-audit-service", repository: "uocp-audit-service", change_detected: false, changed_file_count: 0 },
      ],
    });
    vi.mocked(runClusterReview).mockResolvedValue({ cluster_review_id: "cluster-1", job_id: "job-1", status: "running", repository_total: 18, cluster_label: "Omnichannel" });
    render(
      <MemoryRouter initialEntries={["/review"]}>
        <Routes>
          <Route path="/review" element={<ReviewPage snapshot={snapshot} />} />
          <Route path="/review/cluster/:clusterReviewId" element={<p>Cluster progress cluster-1</p>} />
        </Routes>
      </MemoryRouter>,
    );
    await user.click(await screen.findByRole("combobox", { name: "Review scope" }));
    await user.click(await screen.findByRole("option", { name: "Service cluster" }));
    await user.click(screen.getByRole("button", { name: "Preview cluster changes" }));
    expect(await screen.findByText("Omnichannel · 18 repositories")).toBeInTheDocument();
    expect(screen.getByText("Changed repositories: 2")).toBeInTheDocument();
    expect(screen.getByText("No changes: 16")).toBeInTheDocument();
    expect(screen.getByText("4 changed files")).toBeInTheDocument();
    expect(screen.queryByText("uocp-audit-service")).not.toBeInTheDocument();
    expect(previewChange).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Review cluster changes" }));
    expect(await screen.findByText("Cluster progress cluster-1")).toBeInTheDocument();
    expect(runReview).not.toHaveBeenCalled();
  });
});
