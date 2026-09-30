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
      services: Record<string, { exposes: Route[]; consumes: { url: string; method: string; type: string }[] }>;
      dependencies: { from: string; to: string; endpoint: string; method: string; confidence: string }[];
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
  running: boolean;
};

export type Update = { repo: string; ecosystem: string; package: string; from: string; to: string };
export type Finding = { repo: string; file: string; name: string; line: number; message: string };
export type Route = { path: string; method: string; framework: string };
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

export async function startRun(path: string): Promise<void> {
  await fetch(path, { method: "POST" });
}
