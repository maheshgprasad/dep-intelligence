import type { ReviewFinding, ReviewReport } from "./api";

export const TARGET_PREVIEW = 3;

export type RankedCandidate = ReviewReport["impact"]["ranked_candidates"][number];

export type ServiceGroup = {
  serviceId: string;
  files: string[];
  direct: boolean;
};

export function serviceGroups(report: ReviewReport): ServiceGroup[] {
  const direct = new Set(
    (report.impact.cluster_downstream?.items ?? []).filter((item) => item.direct).map((item) => item.service_id),
  );
  const origin = report.review_scope?.origin_service_id || report.change.service_id || "";
  const groups: ServiceGroup[] = [];
  for (const candidate of report.impact.ranked_candidates ?? []) {
    const serviceId = candidate.service_id || "unattributed";
    if (serviceId === origin) continue;
    let group = groups.find((item) => item.serviceId === serviceId);
    if (!group) {
      group = { serviceId, files: [], direct: direct.has(serviceId) };
      groups.push(group);
    }
    const file = candidate.file_path || candidate.name || "";
    if (file && !group.files.includes(file)) group.files.push(file);
  }
  groups.sort((left, right) => Number(right.direct) - Number(left.direct));
  return groups;
}

export function primaryTarget(report: ReviewReport): string {
  return serviceGroups(report)[0]?.serviceId || "";
}

export function blockerCount(report: ReviewReport): number {
  return report.gate.blocking_findings?.length ?? 0;
}

export function findingRank(finding: ReviewFinding, report: ReviewReport): number {
  const blocking = report.gate.blocking_findings?.includes(finding.finding_id);
  if (blocking || finding.category === "contract_break" || finding.category === "verification_failure" || finding.severity === "critical") return 0;
  const origin = evidenceOrigin(finding);
  if (origin === "both") return 1;
  if (origin === "structural_only" || finding.category === "cross_repo_impact") return 2;
  const direct = (report.impact.cluster_downstream?.items ?? []).some((item) => item.direct && item.service_id === finding.service_id);
  if (direct) return 3;
  if (origin === "temporal_only") return 5;
  return 4;
}

export function orderedFindings(report: ReviewReport): ReviewFinding[] {
  return [...report.findings].sort((left, right) => findingRank(left, report) - findingRank(right, report) || left.title.localeCompare(right.title));
}

function evidenceOrigin(finding: ReviewFinding): string {
  const structured = finding.structured?.evidence_origin;
  if (structured) return structured;
  for (const item of finding.evidence ?? []) {
    const origin = item.structured_data?.evidence_origin;
    if (typeof origin === "string") return origin;
  }
  return "";
}
