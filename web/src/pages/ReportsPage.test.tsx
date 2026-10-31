import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ReportsPage } from "./ReportsPage";

vi.mock("../api", () => ({
  listIntelligenceReports: vi.fn().mockResolvedValue([]),
  getServiceClusters: vi.fn().mockResolvedValue({
    clusters: [{ cluster_id: "svc-cluster-fixture", label: "AUTH service cluster", service_count: 3, relationship_count: 2, services: ["auth-service", "user-service", "reporting-service"] }],
  }),
  createIntelligenceReport: vi.fn(),
}));

const snapshot = {
  cluster: {
    services: [
      { id: "auth-service" },
      { id: "tooling-repo" },
    ],
    stale_services: [],
  },
} as never;

describe("Reports page", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("offers workspace, service cluster, and repository scopes", async () => {
    render(<MemoryRouter><ReportsPage snapshot={snapshot} /></MemoryRouter>);
    expect(screen.getByRole("tab", { name: "Entire workspace" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Service cluster" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Single repository" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Service cluster" }));
    expect(await screen.findByText(/AUTH service cluster — 3 services · 2 confirmed calls/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Single repository" }));
    const repository = await screen.findByRole("combobox", { name: "Repository" });
    await userEvent.click(repository);
    expect(await screen.findByRole("option", { name: "tooling-repo" })).toBeInTheDocument();
  });
});
