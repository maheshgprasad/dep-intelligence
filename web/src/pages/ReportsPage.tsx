import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, ContentSwitcher, Dropdown, Switch } from "@carbon/react";
import { listIntelligenceReports, type AppState, type IntelligenceReportSummary } from "../api";
import { GenerateReportButton } from "../components/GenerateReportButton";
import { NamiDataTable } from "../components/NamiDataTable";
import { PageHeader } from "../components/PageHeader";

export function ReportsPage({ snapshot }: { snapshot: AppState | null }) {
  const navigate = useNavigate();
  const services = snapshot?.cluster?.services ?? [];
  const [scope, setScope] = useState<"cluster" | "repository">("cluster");
  const [service, setService] = useState(services[0]?.id || "");
  const [reports, setReports] = useState<IntelligenceReportSummary[]>([]);

  useEffect(() => {
    void listIntelligenceReports().then(setReports).catch(() => setReports([]));
  }, []);

  return (
    <div className="stack-gap">
      <PageHeader
        title="Reports"
        description="Point-in-time intelligence for one repository or the whole workspace. Generation uses the published snapshot and does not rerun ranking or review."
      />
      <ContentSwitcher
        selectedIndex={scope === "repository" ? 1 : 0}
        onChange={({ index }) => setScope(index === 1 ? "repository" : "cluster")}
      >
        <Switch>Entire workspace</Switch>
        <Switch>Single repository</Switch>
      </ContentSwitcher>
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
      <GenerateReportButton scope={scope} service={service} staleCount={snapshot?.cluster?.stale_services?.length || 0} />
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
          scope: item.scope === "cluster" ? "Workspace report" : "Repository report",
          service: item.service || "Entire workspace",
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
