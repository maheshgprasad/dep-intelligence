export type PackageUse = { name: string; version: string; ecosystem: string };

export type Snapshot = {
  languages: {
    repositories: Record<
      string,
      { primary_language: string; language_key: string; color: string; confidence: string; total_files: number }
    >;
  } | null;
  matrix: {
    meta: { total_repos: number; total_packages: number };
    repositories: Record<string, PackageUse[]>;
    common_packages: { name: string; ecosystem: string; repos: string[] }[];
    unique_packages: { name: string; ecosystem: string; repo: string; version: string }[];
    version_mismatches: { name: string; ecosystem: string; repos: string[]; versions: Record<string, string> }[];
  } | null;
  updates: {
    summary: { total: number; major: number; minor: number; patch: number };
    updates: { major: Update[]; minor: Update[]; patch: Update[] };
  } | null;
  review: {
    summary: { total: number; dead_code: number; undefined_variables: number };
    findings: {
      dead_code: Finding[];
      undefined_variables: Finding[];
    };
  } | null;
  coverage: {
    repositories: Record<
      string,
      { language: string; status: string; message: string; percent: number | null; files: { path: string; percent: number | null }[] }
    >;
  } | null;
  apis: {
    summary: { total_services: number; total_dependencies: number };
    api_dependencies: {
      services: Record<string, { exposes: Route[]; consumes: { url: string; method: string; type: string; file?: string }[] }>;
      dependencies: { from: string; to: string; endpoint: string; method: string; confidence: string }[];
      diagnostics?: { code: string; message: string; service?: string }[];
    };
  } | null;
  vulnerabilities: {
    meta?: { scanned: number; total: number; suggestions: number };
    findings: {
      repo: string;
      id: string;
      package: string;
      version: string;
      summary: string;
      severity: string;
      suggestion?: string;
      fixed?: string;
    }[];
    scanned?: { repo: string; package: string; version: string; advisory_count: number; status: string }[];
  } | null;
  cves: { issues: { repo: string; title: string; url: string; state: string; cve: string }[]; summary: { message?: string } } | null;
  security: { summary: { github_issues: number; advisories: number } } | null;
  security_markdown: string;
  graphs: { repos: GraphRepo[] } | null;
  graph_details: Record<string, { crg: CrgBundle | null; cochange: Cochange | null }>;
  analysis_status?: { status?: string; message?: string; results?: { tool: string; success?: boolean; message?: string }[] } | null;
  cluster?: ClusterSummary | null;
  running: boolean;
};

export type ClusterSummary = {
  version: string;
  generated_at?: string;
  node_count: number;
  edge_count: number;
  contract_count: number;
  unresolved_count: number;
  stale_services?: string[];
  config_errors?: string[];
  notices?: string[];
  allowlist?: string[];
  services?: {
    id: string;
    quality: string;
    error?: string;
    symbols: number;
    index_revision?: string | null;
    workspace_head?: string | null;
    observed_at?: string;
  }[];
  diagnostics?: { code: string; message: string; severity?: string }[];
};

export type Update = { repo: string; ecosystem: string; package: string; from: string; to: string };
export type Finding = { repo: string; file: string; name: string; line: number; message: string };
export type Route = { path: string; method: string; framework: string; file?: string };
export type GraphRepo = {
  slug: string;
  name: string;
  url?: string;
  checkout?: string;
  error?: string;
  stats?: {
    files?: number;
    edges?: number;
    communities?: number;
    files_count?: number;
    total_nodes?: number;
    total_edges?: number;
    languages?: string[];
  };
};
export type CrgPayload = Record<string, unknown>;
export type CrgBundle = { meta?: { source?: string; generated_at?: string }; tools?: Record<string, CrgPayload> };
export type Cochange = {
  window_label: string;
  commit_count: number;
  author_count: number;
  message?: string;
  hotspots: { file: string; commits: number; additions: number; deletions: number; churn_score: number }[];
  cochange: { file_a: string; file_b: string; commits: number; coupling: number; is_structural?: boolean }[];
};

export async function queryGraph(slug: string, tool: string, args: Record<string, unknown>): Promise<CrgPayload> {
  const response = await fetch("/api/graphs/query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slug, tool, arguments: args }),
  });
  const body = (await response.json().catch(() => ({}))) as { detail?: string };
  if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Graph query failed");
  return body as CrgPayload;
}

export async function getSnapshot(): Promise<Snapshot> {
  const response = await fetch("/api/snapshot");
  if (!response.ok) throw new Error("snapshot failed");
  return response.json();
}

export type ImpactRow = {
  key: string;
  structural_score: number;
  service_id: string;
  name: string;
  qualified_name: string;
  file_path: string;
  line_start: number | null;
  category: string;
  confidence: number | null;
  path: {
    relation: string;
    direction: string;
    weight: number;
    reason: string;
    source?: string;
    target?: string;
    source_name?: string;
    target_name?: string;
    evidence?: { status?: string; file?: string; line?: number; source?: string };
  }[];
};

export type ClusterNode = {
  key: string;
  service_id: string;
  name: string;
  qualified_name: string;
  file_path: string;
  line_start: number | null;
  category: string;
  kind: string;
};

export async function clusterGraph(params?: {
  service?: string;
  kind?: string;
  q?: string;
  limit?: number;
  offset?: number;
}): Promise<{ summary: ClusterSummary; page: { nodes: ClusterNode[]; edges?: { source: string; target: string; relation: string }[]; total: number; truncated: boolean } }> {
  const query = new URLSearchParams();
  if (params?.service) query.set("service", params.service);
  if (params?.kind) query.set("kind", params.kind);
  if (params?.q) query.set("q", params.q);
  query.set("limit", String(params?.limit ?? 5000));
  if (params?.offset) query.set("offset", String(params.offset));
  const response = await fetch(`/api/cluster/graph?${query.toString()}`);
  if (!response.ok) throw new Error("cluster graph failed");
  return response.json();
}

export async function clusterContracts(): Promise<{ version: string; contracts: { key: string; name: string; service_id: string; category: string; qualified_name: string }[] }> {
  const response = await fetch("/api/cluster/contracts");
  if (!response.ok) throw new Error("contracts failed");
  return response.json();
}

export type HybridPathEvidence = {
  available: boolean;
  score: number;
  support: number;
  source_opportunities: number;
  directional_rate: number;
  recency: number;
  evidence_kind: string;
  observation: string;
  source: string;
  target: string;
};

export type HybridCandidate = {
  key: string | { service_id: string; repository: string; file_path: string };
  rank?: number;
  sort_key?: string;
  service_id: string;
  name: string;
  qualified_name: string;
  file_path: string;
  line_start: number | null;
  category: string;
  kind: string;
  hybrid_score: number;
  evidence_origin: string;
  why_ranked: string[];
  structural: {
    available: boolean;
    score: number;
    hops: number;
    aggregation?: string;
    strongest_symbol?: { name: string; qualified_name: string; line_start: number | null } | null;
    impacted_symbols?: { name: string; kind?: string; line_start: number | null; structural_score: number }[];
    path: ImpactRow["path"];
  };
  temporal: {
    available: boolean;
    score: number;
    status?: string;
    raw_score?: number;
    coverage?: number | null;
    coverage_adjusted_score?: number | null;
    evidence_quality?: string;
    support: number;
    directional_rate: number;
    recency: number;
    evidence_kind: string;
    observation: string;
    last_supported_at: string | null;
    path_temporal_coverage: number;
    available_segments: number;
    total_segments: number;
    temporal_path_evidence: HybridPathEvidence[];
  };
};

export type HybridImpactResponse = {
  analysis_id: string;
  graph_version: string;
  mode: string;
  module?: string;
  module_abbrev?: string;
  module_version?: string;
  module_description?: string;
  ranking_unit?: string;
  root?: { service_id: string; repository: string; file_path: string; symbols: string[] };
  causal_validity?: { temporal: boolean; structural: boolean; overall: boolean };
  fusion?: { method: string; weights?: { structural: number; temporal: number } };
  experiment: string;
  score_kind: string;
  score_meaning: string;
  truncated: boolean;
  truncation_reason: string;
  temporal_provider: string;
  temporal_cutoff: string | null;
  weights: { structural: number; temporal: number; note?: string };
  candidates: HybridCandidate[];
  diagnostics: { code: string; message: string }[];
  limitations: string[];
};

export async function nrmCapabilities(): Promise<{
  module_version: string;
  temporal_providers: { id: string; available: boolean; reason?: string }[];
  defaults: { structural_weight: number; temporal_weight: number; cross_repo_window_days: number };
}> {
  const response = await fetch("/api/cluster/nrm/capabilities");
  if (!response.ok) throw new Error("NRM capabilities failed");
  return response.json();
}

export async function runHybridImpact(body: Record<string, unknown>): Promise<HybridImpactResponse> {
  return postCluster("/api/cluster/hybrid-impact", body);
}

async function postCluster<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    const message = typeof detail === "string" ? detail : detail?.detail || "Impact query failed";
    const error = new Error(message) as Error & { status?: number; payload?: unknown };
    error.status = response.status;
    error.payload = detail;
    throw error;
  }
  return payload as T;
}

export type ReviewFinding = {
  finding_id: string;
  fingerprint?: string;
  category: string;
  severity: string;
  title: string;
  claim: string;
  file_path?: string | null;
  line_start?: number | null;
  evidence_strength: string;
  verification_status?: string;
  policy_id?: string | null;
  evidence?: { summary: string; source: string; structured_data?: Record<string, unknown> }[];
};

export type ReviewReport = {
  review_id: string;
  gate: { decision: string; reason_codes: string[]; blocking_findings: string[]; warning_findings: string[]; review_targets: string[]; evidence_complete: boolean };
  change: { repository?: string; service_id?: string; base_sha?: string | null; head_sha?: string | null; graph_alignment?: string; graph_version?: string; temporal_cutoff?: string | null };
  summary: { text: string };
  changed_files: { new_path?: string | null; old_path?: string | null; change_type: string; additions: number | null; deletions: number | null; classifications: string[] }[];
  changed_symbols: { qualified_name: string; file_path: string; kind: string; name?: string }[];
  contract_changes: { kind: string; method: string; path: string; breaking: boolean; message: string; file_path?: string }[];
  impact: {
    affected_services: string[];
    ranked_candidates: {
      service_id: string;
      file_path: string;
      name?: string;
      evidence_origin: string;
      structural_score?: number;
      temporal_score?: number;
      hybrid_score?: number;
      statement?: string;
      affected_by_roots?: { qualified_name?: string; service_id?: string }[];
      structural?: { path?: { relation?: string; source?: string; target?: string; source_name?: string; target_name?: string }[] };
    }[];
  };
  findings: ReviewFinding[];
  verification: { id: string; command: string[]; status: string; duration_ms: number; exit_code: number | null; output: string }[];
  policies: { policy_id: string; applied: boolean; result: string; severity: string; evidence: string }[];
  diagnostics: { code: string; message: string }[];
  limitations: string[];
  performance?: { total_ms?: number };
  score_note?: string;
};

export async function reviewConfig(repository = ""): Promise<{ model_configured: boolean; policies: { id: string; severity: string; check: string }[]; diagnostics: { message: string }[]; score_note: string }> {
  const response = await fetch(`/api/review/config?repository=${encodeURIComponent(repository)}`);
  if (!response.ok) throw new Error("Review configuration failed");
  return response.json();
}

export async function runReview(body: Record<string, unknown>): Promise<ReviewReport & { job_id?: string; status?: string }> {
  const response = await fetch("/api/review/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" && "message" in detail
          ? String((detail as { message?: string }).message)
          : response.status === 409
            ? "The published graph is older than this change. Refresh intelligence, or review in Exact mode."
            : "The review could not be started.";
    throw new Error(message);
  }
  return payload;
}

export async function getReview(reviewId: string): Promise<ReviewReport> {
  const response = await fetch(`/api/review/${reviewId}`);
  if (response.status === 404) throw new Error("This review is not stored.");
  if (!response.ok) throw new Error("The review could not be loaded.");
  return response.json();
}

export async function reviewJob(jobId: string): Promise<{ status: string; result?: ReviewReport; error?: string }> {
  const response = await fetch(`/api/jobs/${jobId}`);
  if (!response.ok) throw new Error("Review job was not found");
  return response.json();
}

export async function sendReviewFeedback(reviewId: string, findingFingerprint: string, status: string): Promise<void> {
  const response = await fetch(`/api/review/${reviewId}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ finding_fingerprint: findingFingerprint, status }),
  });
  if (!response.ok) throw new Error("Feedback was not stored");
}

export async function refreshIntelligence(): Promise<{ started: boolean; message?: string }> {
  const response = await fetch("/api/intelligence/refresh", { method: "POST" });
  if (!response.ok) throw new Error("Intelligence could not be refreshed.");
  return response.json();
}

export async function refreshCluster(): Promise<{ job_id: string; status: string }> {
  const response = await fetch("/api/cluster/refresh", { method: "POST" });
  if (!response.ok) throw new Error("refresh failed");
  return response.json();
}
