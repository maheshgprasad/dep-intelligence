import type { Snapshot } from "./api";

export type Readiness = "Current" | "Stale" | "Partial" | "Unavailable" | "Refreshing";

export type IntelligenceView = {
  overall: Readiness;
  cluster: Readiness;
  history: Readiness;
  contracts: Readiness;
  stale: number;
  unresolved: number;
  refreshedAt: string;
  attention: string[];
};

export function intelligenceView(snapshot: Snapshot | null, refreshing: boolean): IntelligenceView {
  const cluster = snapshot?.cluster;
  const graphs = snapshot?.graphs?.repos ?? [];
  const historyPresent = graphs.some((repo) => (snapshot?.graph_details[repo.slug]?.cochange?.commit_count ?? 0) > 0);
  const graphErrors = graphs.filter((repo) => repo.error).length;
  const stale = cluster?.stale_services?.length ?? 0;
  let clusterStatus: Readiness = "Unavailable";
  if (cluster?.version) clusterStatus = stale > 0 || graphErrors > 0 ? "Stale" : "Current";
  else if (graphs.length) clusterStatus = "Partial";
  const history: Readiness = historyPresent ? "Current" : graphs.length ? "Partial" : "Unavailable";
  const contracts: Readiness = !cluster?.version ? "Unavailable" : (cluster.contract_count ?? 0) > 0 ? "Current" : "Partial";
  const attention: string[] = [];
  if ((cluster?.unresolved_count ?? 0) > 0) {
    attention.push(`${cluster?.unresolved_count} unresolved reference${cluster?.unresolved_count === 1 ? "" : "s"}`);
  }
  for (const service of cluster?.stale_services ?? []) attention.push(`${service} graph is stale`);
  for (const service of cluster?.services ?? []) {
    if (service.error) attention.push(`${service.id} graph unavailable`);
    else if (service.quality && service.quality !== "ok") attention.push(`${service.id} graph is ${service.quality}`);
  }
  for (const notice of cluster?.config_errors ?? []) attention.push(notice);
  const overall: Readiness = refreshing
    ? "Refreshing"
    : [clusterStatus, history, contracts].includes("Unavailable") && !cluster?.version
      ? "Unavailable"
      : attention.length
        ? "Partial"
        : clusterStatus === "Current" && history === "Current"
          ? "Current"
          : "Partial";
  return {
    overall,
    cluster: refreshing ? "Refreshing" : clusterStatus,
    history: refreshing ? "Refreshing" : history,
    contracts: refreshing ? "Refreshing" : contracts,
    stale,
    unresolved: cluster?.unresolved_count ?? 0,
    refreshedAt: cluster?.generated_at || "Not refreshed yet",
    attention,
  };
}
