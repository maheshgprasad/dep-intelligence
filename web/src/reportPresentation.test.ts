import { describe, expect, it } from "vitest";
import appSource from "./App.tsx?raw";
import {
  PRODUCT_ROUTES,
  evidenceLine,
  legacySummary,
  providerDiscoveryText,
  rankingLine,
  reportTarget,
  reportType,
  validationLines,
} from "./reportPresentation";

describe("report presentation", () => {
  it("names the three report scopes in user language", () => {
    expect(reportType("workspace")).toBe("Workspace report");
    expect(reportType("cluster")).toBe("Workspace report");
    expect(reportType("service_cluster")).toBe("Service cluster report");
    expect(reportType("repository")).toBe("Repository report");
    expect(reportTarget({ scope: "workspace" })).toBe("Entire workspace");
    expect(reportTarget({ scope: "service_cluster", label: "UOCP service cluster" })).toBe("UOCP service cluster");
    expect(reportTarget({ scope: "repository", service: "tooling-repo" })).toBe("tooling-repo");
  });

  it("keeps failed validation from sounding confirmed", () => {
    const lines = validationLines({ validation_status: "failed", validation: "Decision logic validated. Safe to proceed." });
    expect(lines.join(" ")).toContain("Decision logic: Validation failed");
    expect(lines.join(" ").toLowerCase()).not.toContain("validated evidence");
    expect(lines.join(" ").toLowerCase()).not.toContain("safe to proceed");
  });

  it("separates historical evidence from ranking context", () => {
    expect(evidenceLine({ source: "Repository history", statement: "The file changed nearby." })).toContain("Evidence: Repository history");
    expect(rankingLine({ source: "Nami Rank", statement: "Nami Rank prioritized this target." })).toContain("Ranking context: Nami Rank");
    expect(evidenceLine({ source: "Repository history", statement: "history" })).not.toContain("Nami Rank");
  });

  it("treats a missing summary version as a legacy summary", () => {
    expect(legacySummary({ review_activity: {} })).toBe(true);
    expect(legacySummary({ review_activity: { summary_version: 2 } })).toBe(false);
  });

  it("does not describe an unscanned provider discovery as zero findings", () => {
    expect(providerDiscoveryText(null)).toContain("Not scanned");
    expect(providerDiscoveryText({ status: "not_scanned" })).not.toContain("0 unique");
    expect(providerDiscoveryText({ status: "complete", unique_unresolved_reference_count: 0 })).toContain("0 unique unresolved provider references");
  });

  it("keeps the permanent product routes in the application", () => {
    for (const route of PRODUCT_ROUTES) {
      expect(appSource).toContain(`path="${route}"`);
    }
  });
});
