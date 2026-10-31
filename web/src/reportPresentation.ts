/** User-facing report labels. These describe stored reports. They do not decide anything. */

export const PRODUCT_ROUTES = [
  "/",
  "/review",
  "/review/:reviewId",
  "/impact",
  "/architecture",
  "/reports",
  "/reports/:reportId",
  "/health",
  "/research",
] as const;

const VALIDATION_LABELS: Record<string, string> = {
  validated: "Validated",
  validated_with_limitations: "Validated with limitations",
  failed: "Validation failed",
  not_available: "Not available",
  unavailable: "Unavailable",
};

export function reportType(scope: string): string {
  if (scope === "cluster" || scope === "workspace") return "Workspace report";
  if (scope === "service_cluster") return "Service cluster report";
  return "Repository report";
}

export function reportTarget(item: { scope: string; label?: string; service?: string | null }): string {
  if (item.scope === "cluster" || item.scope === "workspace") return "Entire workspace";
  return item.label || item.service || "—";
}

export function scopeDescription(report: { scope?: string; identity?: { cluster_label?: string; service?: string } }): string {
  if (report.scope === "service_cluster") {
    return `Service cluster report${report.identity?.cluster_label ? ` · ${report.identity.cluster_label}` : ""} · Point-in-time snapshot`;
  }
  if (report.scope === "repository") {
    return `Repository intelligence report${report.identity?.service ? ` · ${report.identity.service}` : ""} · Point-in-time snapshot`;
  }
  return "Workspace intelligence report · Point-in-time snapshot";
}

export function legacySummary(report: { review_activity?: { summary_version?: number } }): boolean {
  return !report.review_activity?.summary_version;
}

export function validationLines(item: {
  validation?: string;
  validation_status?: string;
  validation_detail?: { status?: string; note?: string };
}): string[] {
  const status = item.validation_status || item.validation_detail?.status;
  if (status && VALIDATION_LABELS[status]) {
    const lines = [`Decision logic: ${VALIDATION_LABELS[status]}`];
    if (item.validation_detail?.note) lines.push(item.validation_detail.note);
    return lines;
  }
  const raw = item.validation || "";
  if (!raw) return [];
  return [raw.toLowerCase().startsWith("decision logic") ? raw : `Decision logic: ${raw}`];
}

export function evidenceLine(entry?: { source?: string; what?: string; statement?: string }): string {
  if (!entry) return "";
  return `Evidence: ${entry.source || "Stored evidence"}. ${entry.what || entry.statement || ""}`.trim();
}

export function rankingLine(ranking?: { source?: string; statement?: string } | null): string {
  if (!ranking?.statement) return "";
  return `Ranking context: ${ranking.source || "Nami Rank"}. ${ranking.statement}`;
}

export function providerDiscoveryText(discovery?: { status?: string; unique_unresolved_reference_count?: number } | null): string {
  if (!discovery || discovery.status === "not_scanned") return "Topology provider discovery: Not scanned.";
  return `${discovery.unique_unresolved_reference_count ?? 0} unique unresolved provider references.`;
}

export function externalTargetNote(targets?: string[]): string {
  if (!targets?.length) return "";
  return "These services are outside the selected Service Cluster. Their appearance here comes from the stored review and does not change cluster membership.";
}
