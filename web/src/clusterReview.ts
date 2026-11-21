export type ClusterRepositoryState = {
  service_id: string;
  repository: string;
  repository_index: number;
  status: string;
  stage: string;
  stage_label: string;
  stage_index: number;
  stage_count: number;
  change_detected: boolean;
  changed_file_count: number;
  review_id: string;
  decision: string;
  validation_status: string;
  error_code: string;
  error_summary: string;
  commits?: { sha: string; committed_at: string; subject: string }[];
  commit_count?: number;
};

export type ClusterReviewCounters = {
  repository_total: number;
  repositories_inspected: number;
  repositories_pending: number;
  repositories_running: number;
  repositories_with_changes: number;
  repositories_no_change: number;
  reviews_completed: number;
  reviews_failed: number;
  repositories_cancelled?: number;
};

export type ClusterAssessment = {
  code: string;
  label: string;
  summary: string;
  verified_blockers: number;
  warnings: number;
  passes: number;
  incomplete: number;
  failed: number;
  repositories_with_changes: number;
  repositories_no_change: number;
  evidenced_service_count: number;
  evidenced_services: string[];
  complete: boolean;
};

export type ClusterReviewState = {
  cluster_review_id: string;
  cluster_id: string;
  cluster_label: string;
  status: string;
  job_id?: string;
  sequence: number;
  cancel_requested?: boolean;
  repository_total: number;
  percent: number;
  counters: ClusterReviewCounters;
  repositories: ClusterRepositoryState[];
  running: ClusterRepositoryState[];
  review_stages: { stage: string; label: string }[];
  assessment: ClusterAssessment | null;
  analysis_stage?: string;
  analysis_label?: string;
  source?: { kind?: string; since?: string; until?: string };
  copy_summary?: string;
  interdependencies?: ClusterInterdependencies | null;
};

export type ClusterEvidenceRef = { review_id?: string; evidence_id: string };

export type ClusterInterdependency = {
  source_service: string;
  target_service: string;
  provider_service?: string;
  consumer_service?: string;
  relationship: string;
  contract_id: string;
  classification: string;
  statement: string;
  required_action?: string;
  evidence_ids?: string[];
  child_evidence_refs?: ClusterEvidenceRef[];
  child_review_ids?: string[];
  path?: { service_id: string; role?: string; contract?: string }[];
  affected_services?: string[];
  before?: { provider_present?: boolean; consumer_reference_present?: boolean };
  after?: { provider_present?: boolean; consumer_reference_present?: boolean };
  old_contract?: string;
  new_contract?: string;
  migration_status?: string;
  migrated_consumers?: string[];
  outstanding_consumers?: string[];
};

export type ClusterInterdependencies = {
  comparison_mode: string;
  comparison_coverage?: {
    services_total?: number;
    services_exact_before?: number;
    services_exact_after?: number;
    services_partial?: string[];
    relationship_types?: Record<string, string>;
  };
  limitations?: string[];
  diagnostic?: { code?: string; stage?: string; message?: string };
  summary: Record<string, number>;
  items: ClusterInterdependency[];
  changed_services?: string[];
  affected_services?: string[];
  unchanged_but_affected_services?: string[];
  temporal_context?: { statement?: string }[];
};

export function applyClusterSnapshot(current: ClusterReviewState | null, incoming: ClusterReviewState): ClusterReviewState {
  if (
    current
    && incoming.cluster_review_id
    && incoming.cluster_review_id === current.cluster_review_id
    && (incoming.sequence ?? 0) < (current.sequence ?? 0)
  ) {
    return current;
  }
  const rows = Array.isArray(incoming.repositories) ? incoming.repositories : current?.repositories ?? [];
  const byId = new Map<string, ClusterRepositoryState>();
  for (const item of rows) {
    const key = item.service_id || item.repository;
    const prior = byId.get(key);
    byId.set(key, prior ? { ...prior, ...item } : item);
  }
  return {
    ...(current ?? incoming),
    ...incoming,
    repositories: [...byId.values()],
  };
}

export function decisionText(decision: string): string {
  if (decision === "pass") return "PASS";
  if (decision === "warn") return "WARN";
  if (decision === "block") return "BLOCK";
  if (decision === "incomplete") return "INCOMPLETE";
  if (!decision) return "Complete";
  return decision.toUpperCase();
}

export function validationText(status: string): string {
  if (status === "validated") return "Validated";
  if (status === "failed") return "Validation failed";
  return status;
}

export function repositoryStatusText(repository: ClusterRepositoryState): string {
  if (repository.status === "pending") return "Waiting";
  if (repository.status === "inspecting") return "Inspecting";
  if (repository.status === "no_change") return "No changes";
  if (repository.status === "reviewing") return "Reviewing";
  if (repository.status === "completed") return decisionText(repository.decision);
  if (repository.status === "failed") return "Failed";
  if (repository.status === "cancelled") return "Cancelled";
  return repository.status;
}

export function repositoryDetail(repository: ClusterRepositoryState): string {
  if (repository.status === "pending") return "Waiting";
  if (repository.status === "no_change") return "No changes";
  if (repository.status === "failed") return repository.error_summary ? `Failed · ${repository.error_summary}` : "Failed";
  if (repository.status === "cancelled") return "Cancelled";
  const files = repository.changed_file_count > 0
    ? `${repository.changed_file_count} changed file${repository.changed_file_count === 1 ? "" : "s"}`
    : "";
  if (repository.status === "inspecting") return [files, "Inspecting", repository.stage_label || "Detecting changes"].filter(Boolean).join(" · ");
  if (repository.status === "reviewing") return [files, "Reviewing", repository.stage_label].filter(Boolean).join(" · ");
  if (repository.status === "completed") {
    const validation = validationText(repository.validation_status);
    return [files, decisionText(repository.decision), validation].filter(Boolean).join(" · ");
  }
  return repositoryStatusText(repository);
}

export function stageState(stages: { stage: string }[], current: string, stage: string): "complete" | "current" | "waiting" {
  const currentIndex = stages.findIndex((item) => item.stage === current);
  const index = stages.findIndex((item) => item.stage === stage);
  if (currentIndex < 0 || index < 0) return "waiting";
  if (index < currentIndex) return "complete";
  if (index === currentIndex) return "current";
  return "waiting";
}

export function stageStateText(state: "complete" | "current" | "waiting"): string {
  if (state === "complete") return "Complete";
  if (state === "current") return "Current";
  return "Waiting";
}
