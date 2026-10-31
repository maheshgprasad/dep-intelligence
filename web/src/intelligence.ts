import type { AppState } from "./api";

export type Readiness = "Current" | "Stale" | "Partial" | "Unavailable" | "Refreshing" | "Missing";

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

const LABELS: Record<string, Readiness> = {
  current: "Current",
  stale: "Stale",
  partial: "Partial",
  unavailable: "Unavailable",
  missing: "Missing",
  failed: "Unavailable",
};

export function intelligenceView(state: AppState | null, refreshing: boolean): IntelligenceView {
  const intelligence = state?.intelligence;
  const attention: string[] = [];
  const providers = intelligence?.provider_discovery;
  if ((providers?.unique_unresolved ?? 0) > 0) {
    attention.push(`${providers?.unique_unresolved} unresolved provider reference${providers?.unique_unresolved === 1 ? "" : "s"}`);
  }
  for (const service of intelligence?.stale_services ?? []) attention.push(`${service} graph is stale`);
  for (const service of state?.cluster?.services ?? []) {
    if (service.error) attention.push(`${service.id} graph unavailable`);
    else if (service.quality && service.quality !== "ok") attention.push(`${service.id} graph is ${service.quality}`);
  }
  for (const notice of state?.cluster?.config_errors ?? []) attention.push(notice);
  const history = intelligence?.history;
  if (history && history.total_services > 0 && history.ready_services < history.total_services) {
    attention.push(`Commit history is ready for ${history.ready_services} of ${history.total_services} repositories`);
  }
  return {
    overall: refreshing ? "Refreshing" : LABELS[intelligence?.overall || "unavailable"] || "Partial",
    cluster: refreshing ? "Refreshing" : LABELS[intelligence?.graph.status || "unavailable"] || "Partial",
    history: refreshing ? "Refreshing" : LABELS[intelligence?.history.status || "unavailable"] || "Partial",
    contracts: refreshing ? "Refreshing" : LABELS[intelligence?.contracts.status || "unavailable"] || "Partial",
    stale: intelligence?.stale_services.length ?? 0,
    unresolved: intelligence?.unresolved ?? 0,
    refreshedAt: intelligence?.refreshed_at || "Not refreshed yet",
    attention,
  };
}
