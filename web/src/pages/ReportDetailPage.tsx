import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Accordion, AccordionItem, InlineNotification, StructuredListBody, StructuredListCell, StructuredListRow, StructuredListWrapper, Tile } from "@carbon/react";
import { getIntelligenceReport, type IntelligenceReport } from "../api";
import { IntelligenceExportMenu } from "../components/IntelligenceExportMenu";
import { PageHeader } from "../components/PageHeader";

export function ReportDetailPage() {
  const { reportId = "" } = useParams();
  const [report, setReport] = useState<IntelligenceReport | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!reportId) return;
    void getIntelligenceReport(reportId)
      .then(setReport)
      .catch((exc: unknown) => setError(exc instanceof Error ? exc.message : "The report could not be loaded."));
  }, [reportId]);

  if (error) return <InlineNotification kind="error" title="Report unavailable" subtitle={error} lowContrast hideCloseButton />;
  if (!report) return <p className="helper">Loading the stored report.</p>;
  const status = report.intelligence_status || {};
  const statusNames: Record<string, string> = {
    structural: "Structural intelligence",
    history: "History",
    contracts: "Contracts",
    health: "Health",
  };
  return (
    <div className="stack-gap">
      <PageHeader
        title={report.executive_summary.headline || "Intelligence report"}
        description={report.scope === "cluster" ? "Cluster report" : `Repository report${report.identity.service ? ` · ${report.identity.service}` : ""}`}
        breadcrumbs={[{ label: "Reports", href: "/reports" }, { label: report.report_id.slice(0, 12) }]}
        actions={<IntelligenceExportMenu reportId={report.report_id} />}
      />
      <Tile className="decision-hero">
        <p className="section-title">{report.executive_summary.readiness_label || "Partial"}</p>
        <p>{report.executive_summary.summary}</p>
        <p className="helper">Generated {report.generated_at}. Graph {report.reproducibility.graph_version || "not recorded"}.</p>
      </Tile>
      <Tile>
        <h2 className="section-title">Intelligence status</h2>
        <StructuredListWrapper>
          <StructuredListBody>
            {Object.entries(status).map(([key, item]) => (
              <StructuredListRow key={key}>
                <StructuredListCell>{statusNames[key] || key}</StructuredListCell>
                <StructuredListCell>{item.label || item.status || "Unavailable"}</StructuredListCell>
              </StructuredListRow>
            ))}
          </StructuredListBody>
        </StructuredListWrapper>
      </Tile>
      {report.scope === "cluster" && report.workspace ? (
        <Tile>
          <h2 className="section-title">Workspace</h2>
          <p>{report.workspace.service_count} services. {report.workspace.relationship_count} cross-service relationships. {report.workspace.contract_count} contracts. {report.workspace.graph_stale} out of date. {report.workspace.unresolved_count} unresolved references.</p>
        </Tile>
      ) : null}
      <Tile>
        <h2 className="section-title">Key observations</h2>
        <ul className="action-list">{(report.inferences || []).map((item) => <li key={item.statement}>{item.statement}</li>)}</ul>
      </Tile>
      {report.intelligence_gaps?.length ? (
        <Tile>
          <h2 className="section-title">Intelligence gaps</h2>
          <ul className="action-list">{report.intelligence_gaps.map((item) => <li key={item}>{item}</li>)}</ul>
        </Tile>
      ) : null}
      <Tile>
        <h2 className="section-title">Recommendations</h2>
        <ul className="action-list">
          {(report.recommendations || []).map((item) => (
            <li key={item.title}><strong>{item.title}</strong><p>{item.reason}</p></li>
          ))}
        </ul>
      </Tile>
      <Accordion>
        <AccordionItem title="Limitations">
          <ul>{(report.limitations || []).map((item) => <li key={item}>{item}</li>)}</ul>
        </AccordionItem>
        <AccordionItem title="Technical details">
          <p>Report {report.report_id}</p>
          <p>Graph version {report.reproducibility.graph_version || "not recorded"}</p>
          {report.identity.repository_sha ? <p>Revision {report.identity.repository_sha}</p> : null}
          <p>{report.reproducibility.report_hash}</p>
          <p className="helper">{report.reproducibility.note}</p>
        </AccordionItem>
      </Accordion>
    </div>
  );
}
