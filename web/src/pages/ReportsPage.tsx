import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, ContentSwitcher, Dropdown, Switch, Tile } from "@carbon/react";
import { getServiceClusters, listIntelligenceReports, releaseDrift, type AppState, type IntelligenceReportSummary, type ServiceClusterSummary } from "../api";
import { GenerateReportButton } from "../components/GenerateReportButton";
import { reportTarget, reportType } from "../reportPresentation";
import { NamiDataTable } from "../components/NamiDataTable";
import { NamiEmptyState } from "../components/NamiEmptyState";
import { PageHeader } from "../components/PageHeader";

type ReportScope = "workspace" | "service_cluster" | "repository";

export function ReportsPage({ snapshot }: { snapshot: AppState | null }) {
  const navigate = useNavigate();
  const services = snapshot?.cluster?.services ?? [];
  const [scope, setScope] = useState<ReportScope>("workspace");
  const [service, setService] = useState(services[0]?.id || "");
  const [clusterId, setClusterId] = useState("");
  const [clusters, setClusters] = useState<ServiceClusterSummary[]>([]);
  const [reports, setReports] = useState<IntelligenceReportSummary[]>([]);
  const [drift, setDrift] = useState<Awaited<ReturnType<typeof releaseDrift>> | null>(null);

  useEffect(() => {
    void listIntelligenceReports().then(setReports).catch(() => setReports([]));
    void releaseDrift().then(setDrift).catch(() => setDrift(null));
    void getServiceClusters().then((payload) => {
      setClusters(payload.clusters || []);
      setClusterId((current) => current || payload.clusters?.[0]?.cluster_id || "");
    }).catch(() => setClusters([]));
  }, []);

  return (
    <div className="stack-gap">
      <PageHeader
        title="Reports"
        description="Point-in-time intelligence for the workspace, one connected service cluster, or one repository. Generation uses the published snapshot and does not rerun ranking or review."
      />
      <ContentSwitcher
        selectedIndex={scope === "service_cluster" ? 1 : scope === "repository" ? 2 : 0}
        onChange={({ index }) => setScope(index === 1 ? "service_cluster" : index === 2 ? "repository" : "workspace")}
      >
        <Switch>Entire workspace</Switch>
        <Switch>Service cluster</Switch>
        <Switch>Single repository</Switch>
      </ContentSwitcher>
      {scope === "service_cluster" ? (
        clusters.length ? (
          <Dropdown
            id="report-cluster"
            titleText="Service cluster"
            label="Choose a service cluster"
            items={clusters}
            itemToString={(item) => item ? `${item.label} — ${item.service_count} services · ${item.relationship_count} confirmed calls` : ""}
            selectedItem={clusters.find((item) => item.cluster_id === clusterId) || clusters[0]}
            onChange={({ selectedItem }) => setClusterId(selectedItem?.cluster_id || "")}
          />
        ) : (
          <NamiEmptyState title="No connected service clusters were detected." description="Use Workspace or Single repository reporting instead." />
        )
      ) : null}
      {scope === "repository" ? (
        <Dropdown
          id="report-service"
          titleText="Repository"
          label="Choose a service"
          items={services}
          itemToString={(item) => item?.id ?? ""}
          selectedItem={services.find((item) => item.id === service) || null}
          onChange={({ selectedItem }) => setService(selectedItem?.id || "")}
        />
      ) : null}
      <Tile>
        <h2 className="section-title">Since the last workspace report</h2>
        <p className="helper">{drift?.note || "Drift is compared with the latest stored workspace report."}</p>
        {drift?.status === "available" ? (
          <>
            <p>{drift.added_count ?? 0} added edges. {drift.removed_count ?? 0} removed edges. Unresolved symbols {drift.unresolved_delta ?? 0}. Newly stale: {(drift.stale || []).join(", ") || "none"}.</p>
            {(drift.added || []).slice(0, 5).map((item) => <p key={`added-${item.from_service}-${item.contract}`}>{item.from_service} → {item.to_service} · {item.contract}</p>)}
          </>
        ) : <p>No earlier workspace report is stored.</p>}
      </Tile>
      <GenerateReportButton scope={scope} service={service} clusterId={clusterId} staleCount={snapshot?.cluster?.stale_services?.length || 0} />
      <NamiDataTable
        title="Recent reports"
        columns={[
          { key: "scope", header: "Type" },
          { key: "service", header: "Target" },
          { key: "readiness", header: "Readiness" },
          { key: "generated_at", header: "Generated" },
          { key: "graph_version", header: "Snapshot" },
          { key: "open", header: "Actions", render: (row) => <Button kind="ghost" size="sm" onClick={() => navigate(`/reports/${row.report_id}`)}>Open</Button> },
        ]}
        rows={reports.map((item) => ({
          id: item.report_id,
          report_id: item.report_id,
          scope: reportType(item.scope),
          service: reportTarget(item),
          readiness: item.readiness_label || "Not recorded",
          generated_at: item.generated_at,
          graph_version: item.graph_version,
        }))}
        emptyTitle="No reports yet"
        emptyDescription="Generate a report from the current published intelligence."
      />
    </div>
  );
}
