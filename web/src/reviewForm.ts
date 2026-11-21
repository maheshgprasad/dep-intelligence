export type ReviewSourceKind = "working_tree" | "staged" | "commit" | "range" | "timeframe" | "github_pr";

export type ReviewFormInput = {
  reviewScope: "repository" | "service_cluster";
  clusterId: string;
  repository: string;
  repositoryLabel: string;
  source: ReviewSourceKind;
  commit: string;
  base: string;
  head: string;
  since: string;
  until: string;
  prUrl: string;
  dirtyFiles: number | null;
  stagedFiles: number | null;
  branch: string;
  verificationCommands: string[];
  verifyRequested: boolean;
  mode: "auto" | "fast" | "exact";
};

export type ReviewFormState = {
  canPreview: boolean;
  canReview: boolean;
  verify: boolean;
  verificationEnabled: boolean;
  sourceMessage: string;
  modeText: string;
  showClusterContext: boolean;
};

const MODE_TEXT = {
  auto: "Uses the published graph when it matches this change, and builds private graph evidence when required.",
  fast: "Uses the published intelligence snapshot.",
  exact: "Builds private graph evidence for this revision and does not publish it.",
} as const;

export function reviewFormState(input: ReviewFormInput): ReviewFormState {
  const verificationEnabled = input.verificationCommands.length > 0;
  const label = input.repositoryLabel || "the selected repository";
  let sourceMessage = "";
  let sourceReady = true;
  const clusterTree = input.reviewScope === "service_cluster" && (input.source === "working_tree" || input.source === "staged");
  if (input.source === "working_tree") {
    if (clusterTree) {
      sourceMessage = "Preview lists every repository in the cluster before review starts.";
      sourceReady = true;
    } else if (input.dirtyFiles === null) sourceReady = false;
    else if (input.dirtyFiles === 0) {
      sourceMessage = `No working-tree changes found in ${label}.`;
      sourceReady = false;
    } else sourceMessage = `${input.dirtyFiles} changed files on ${input.branch || "the current branch"}`;
  } else if (input.source === "staged") {
    if (clusterTree) {
      sourceMessage = "Preview lists every repository in the cluster before review starts.";
      sourceReady = true;
    } else if (input.stagedFiles === null) sourceReady = false;
    else if (input.stagedFiles === 0) {
      sourceMessage = `No staged changes found in ${label}.`;
      sourceReady = false;
    } else sourceMessage = `${input.stagedFiles} staged files`;
  } else if (input.source === "commit") sourceReady = input.commit.trim().length > 0;
  else if (input.source === "range") sourceReady = input.base.trim().length > 0 && input.head.trim().length > 0;
  else if (input.source === "timeframe") {
    const since = input.since.trim();
    const until = input.until.trim();
    if (!since || !until) sourceReady = false;
    else if (since > until) {
      sourceMessage = "The timeframe ends before it starts.";
      sourceReady = false;
    } else sourceMessage = `Commits from ${since} through ${until}. History comes from PyDriller. The existing review covers the net change in that window.`;
  } else sourceReady = input.prUrl.trim().length > 0;
  const originReady = input.source === "github_pr" && input.reviewScope === "repository" ? true : input.repository.length > 0;
  const clusterReady = input.reviewScope !== "service_cluster" || input.clusterId.length > 0;
  const ready = sourceReady && originReady && clusterReady;
  return {
    canPreview: ready,
    canReview: ready,
    verify: verificationEnabled && input.verifyRequested,
    verificationEnabled,
    sourceMessage,
    modeText: MODE_TEXT[input.mode],
    showClusterContext: input.reviewScope === "service_cluster" && ready,
  };
}

export function scopeColumnLabel(reviewScope?: string, clusterLabel?: string): string {
  if (reviewScope === "service_cluster") return `Service cluster · ${clusterLabel || "cluster"}`;
  return "Individual repository";
}
