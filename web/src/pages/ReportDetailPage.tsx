import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Accordion, AccordionItem, InlineLoading, InlineNotification, StructuredListBody, StructuredListCell, StructuredListRow, StructuredListWrapper, Tile } from "@carbon/react";
import { getIntelligenceReport, type IntelligenceReport } from "../api";
import { evidenceLine, externalTargetNote, legacySummary, providerDiscoveryText, rankingLine, scopeDescription, validationLines } from "../reportPresentation";
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
  if (!report) return <InlineLoading description="Loading stored report" />;
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
        description={scopeDescription(report)}
        breadcrumbs={[{ label: "Reports", href: "/reports" }, { label: report.report_id.slice(0, 12) }]}
        actions={<IntelligenceExportMenu reportId={report.report_id} />}
      />
      <Tile className="decision-hero">
        <p className="section-title">{report.executive_summary.readiness_label || "Partial"}</p>
        <p>{report.executive_summary.summary}</p>
        <p className="helper">Generated {report.generated_at}. Graph {report.reproducibility.graph_version || "not recorded"}.</p>
        <p className="helper">This report describes published Nami intelligence. It is not a Boundary Gate decision for a specific change.</p>
      </Tile>
      {legacySummary(report) ? <p className="helper">This report was generated before enriched review summaries were stored.</p> : null}
      {report.scope === "cluster" ? <p className="helper">This stored report uses the legacy scope name cluster. That name means Workspace.</p> : null}
      <Attention items={report.recommendations || []} />
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
      {(report.scope === "cluster" || report.scope === "workspace" || report.scope === "service_cluster") && report.workspace ? (
        <Tile>
          <h2 className="section-title">{report.scope === "service_cluster" ? "Service cluster" : "Workspace"}</h2>
          <p>{report.workspace.service_count} services. {report.workspace.relationship_count} confirmed cross-service calls. {report.workspace.contract_count} contracts. {report.workspace.graph_stale} out of date. {providerDiscoveryText(report.provider_discovery)}</p>
          {report.scope === "service_cluster" ? (
            <>
              <p>Cluster {report.identity.cluster_label || "Connected service group"}. Member services {report.identity.services?.length ?? report.workspace.service_count}. Confirmed internal calls {report.workspace.relationship_count}.</p>
              <p className="helper">This report contains one connected component of confirmed cross-service relationships. Repositories outside this component are not included. Use the Workspace report for the complete repository allowlist.</p>
              {report.identity.services?.length ? <p>Cluster membership: {report.identity.services.join(", ")}</p> : null}
            </>
          ) : null}
          {report.scope !== "service_cluster" && report.service_clusters?.items?.length ? (
            <>
              <h3 className="section-title">Detected service clusters</h3>
              <ul className="action-list">
                {report.service_clusters.items.map((item) => <li key={item.cluster_id}>{item.label}. {item.service_count} services. {item.relationship_count} confirmed calls.</li>)}
              </ul>
            </>
          ) : null}
          {report.scope !== "service_cluster" && report.standalone?.length ? (
            <>
              <h3 className="section-title">Standalone repositories</h3>
              <p>{report.standalone.map((item) => item.service_id).join(", ")}</p>
              <p className="helper">A standalone repository or service has no confirmed cross-service relationship in this stored snapshot.</p>
            </>
          ) : null}
        </Tile>
      ) : null}
      <StoredReport report={report} />
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
      <ReviewActivity activity={report.review_activity} />
      <Tile>
        <h2 className="section-title">How to interpret this report</h2>
        <ul className="action-list">
          <li>Dependency reach is not a probability of failure.</li>
          <li>Historical change association does not prove a structural dependency.</li>
          <li>Workspace, Service Cluster, and Repository readiness describe intelligence completeness. They are not Boundary Gate decisions.</li>
          <li>Missing or unscanned data is reported separately from clean results.</li>
        </ul>
      </Tile>
      <Accordion>
        <AccordionItem title="Limitations">
          <ul>{(report.limitations || []).map((item) => <li key={item}>{item}</li>)}</ul>
        </AccordionItem>
        <AccordionItem title="Technical details">
          <p>Report {report.report_id}</p>
          {report.identity.cluster_id ? <p>Service cluster {report.identity.cluster_id}</p> : null}
          <p>Graph version {report.reproducibility.graph_version || "not recorded"}</p>
          {report.identity.repository_sha ? <p>Revision {report.identity.repository_sha}</p> : null}
          <p>{report.reproducibility.report_hash}</p>
          <p className="helper">{report.reproducibility.note}</p>
        </AccordionItem>
      </Accordion>
    </div>
  );
}

const DECISION_LABEL: Record<string, string> = {
  pass: "Ready to proceed",
  warn: "Review recommended",
  block: "Action required before proceeding",
  incomplete: "More evidence is needed",
};

function ReviewActivity({ activity }: { activity: IntelligenceReport["review_activity"] }) {
  const counts = activity?.counts || {};
  const reviews = (activity?.attention_reviews || activity?.reviews || []).filter((item) => item.decision !== "pass").slice(0, 10);
  const omitted = activity?.detail_omitted || 0;
  return (
    <Tile>
      <h2 className="section-title">Review activity</h2>
      <p>Pass {counts.pass ?? 0}. Warn {counts.warn ?? 0}. Block {counts.block ?? 0}. Incomplete {counts.incomplete ?? 0}.</p>
      <p className="helper">{activity?.note || "Counts include all stored reviews that matched this report scope at generation time."}</p>
      <h3 className="section-title">Reviews requiring attention</h3>
      {omitted > 0 ? <p className="helper">Showing {reviews.length} of {reviews.length + omitted} attention-bearing reviews.</p> : null}
      {reviews.length === 0 ? <p>No attention-bearing reviews matched this report.</p> : null}
      {reviews.map((item) => {
        const files = [...new Set((item.targets?.items || []).map((target) => target.file).filter(Boolean))].slice(0, 5);
        return (
          <div key={item.review_id} className="stack-gap">
            <h3 className="section-title">{(item.decision || "").toUpperCase()} · {item.headline || DECISION_LABEL[item.decision || ""]}</h3>
            {item.service ? <p>Service {item.service}</p> : null}
            {item.primary_rule ? <p>Rule {item.primary_rule.title} {item.primary_rule.rule_id}</p> : null}
            {item.reason_codes?.length ? <p className="helper">Reason {item.reason_codes.join(", ")}</p> : null}
            {files.length ? <p>Review targets: {item.targets?.unique_file_count || files.length} files · {item.targets?.total_findings || 0} supporting findings. {files.join(", ")}{(item.targets?.unique_file_count || 0) > files.length ? ` + ${(item.targets?.unique_file_count || 0) - files.length} more` : ""}</p> : null}
            {item.why_it_matters ? <p>Why it matters: {item.why_it_matters}</p> : null}
            {(item.evidence || []).slice(0, 1).map((entry) => (
              <p key={`${item.review_id}-evidence`}>{evidenceLine(entry)}</p>
            ))}
            {rankingLine(item.ranking_context) ? <p>{rankingLine(item.ranking_context)}</p> : null}
            {item.cross_cluster_targets?.length ? (
              <>
                <p>External review targets: {item.cross_cluster_targets.join(", ")}</p>
                <p className="helper">{externalTargetNote(item.cross_cluster_targets)}</p>
              </>
            ) : null}
            {validationLines(item).map((line) => <p key={line}>{line}</p>)}
            {item.next_action ? <p>Recommended action: {item.next_action}</p> : null}
            {item.review_id ? <Link to={`/review/${item.review_id}`}>Open full review</Link> : null}
          </div>
        );
      })}
    </Tile>
  );
}

function Attention({ items }: { items: { title: string; reason: string }[] }) {
  if (!items.length) return null;
  return (
    <Tile>
      <h2 className="section-title">What needs attention</h2>
      <ul className="action-list">
        {items.slice(0, 5).map((item) => <li key={item.title}><strong>{item.title}</strong><p>{item.reason}</p></li>)}
      </ul>
    </Tile>
  );
}

function StoredReport({ report }: { report: IntelligenceReport }) {
  if (report.scope === "cluster" || report.scope === "workspace" || report.scope === "service_cluster") return <WorkspaceSections report={report} />;
  return <RepositorySections report={report} />;
}

function WorkspaceSections({ report }: { report: IntelligenceReport }) {
  const rows = report.relationships?.items || [];
  const shown = rows.slice(0, 24);
  const contracts = report.contracts?.table || [];
  const provenance = report.relationships?.provenance || {};
  return (
    <>
      <Tile>
        <h2 className="section-title">Service catalogue</h2>
        {report.services?.length ? (
          <StructuredListWrapper>
            <StructuredListBody>
              <StructuredListRow head>
                <StructuredListCell head>Service</StructuredListCell>
                <StructuredListCell head>Structure</StructuredListCell>
                <StructuredListCell head>History</StructuredListCell>
                <StructuredListCell head>Health</StructuredListCell>
                <StructuredListCell head>Downstream</StructuredListCell>
              </StructuredListRow>
              {report.services.map((item) => (
                <StructuredListRow key={item.service}>
                  <StructuredListCell>{item.service}</StructuredListCell>
                  <StructuredListCell>{item.graph_label}</StructuredListCell>
                  <StructuredListCell>{item.history_label}</StructuredListCell>
                  <StructuredListCell>{item.health_label}</StructuredListCell>
                  <StructuredListCell>{item.downstream}</StructuredListCell>
                </StructuredListRow>
              ))}
            </StructuredListBody>
          </StructuredListWrapper>
        ) : <p>No services are recorded in this snapshot.</p>}
      </Tile>
      <Tile>
        <h2 className="section-title">{report.scope === "service_cluster" ? "Internal cross-service relationships" : "Cross-service relationships"}</h2>
        {shown.length ? shown.map((item) => (
          <p key={`${item.from_service}-${item.contract}-${item.to_service}`}>{item.from_service} → {item.to_service} · {item.contract} · Provenance {item.provenance || "recorded"}{item.resolution ? ` · Resolution ${item.resolution}` : ""}</p>
        )) : <p>No cross-service relationships are recorded in this snapshot.</p>}
        {rows.length > shown.length ? <p className="helper">Showing {shown.length} of {report.relationships?.total || rows.length} relationships. Use the JSON or Evidence Package for the stored set.</p> : null}
      </Tile>
      <Tile>
        <h2 className="section-title">Contracts</h2>
        {contracts.length ? contracts.slice(0, 24).map((item) => (
          <p key={`${item.provider}-${item.contract}`}>{item.provider} provides {item.contract}. Consumers: {(item.consumers || []).slice(0, 3).join(", ") || "none recorded"}{(item.consumers || []).length > 3 ? `, +${item.consumers.length - 3} more` : ""}.</p>
        )) : <p>No contracts are recorded in this snapshot.</p>}
      </Tile>
      <Tile>
        <h2 className="section-title">Dependency concentration</h2>
        <p>{report.concentration?.note || "Dependency concentration describes confirmed reach. It is not a risk score."}</p>
        {report.concentration?.widest_downstream ? <p>Widest downstream reach: {report.concentration.widest_downstream.service} ({report.concentration.widest_downstream.count}).</p> : null}
        {report.concentration?.most_upstream ? <p>Most upstream dependencies: {report.concentration.most_upstream.service} ({report.concentration.most_upstream.count}).</p> : null}
        {report.concentration?.widest_contract ? <p>Contract with the most consumers: {report.concentration.widest_contract.contract} on {report.concentration.widest_contract.provider} ({report.concentration.widest_contract.consumers}).</p> : null}
      </Tile>
      <Tile>
        <h2 className="section-title">Relationship provenance</h2>
        {Object.keys(provenance).length ? Object.entries(provenance).map(([key, count]) => <p key={key}>{key}: {count}</p>) : <p>Provenance counts were not stored for this snapshot.</p>}
        <p className="helper">These categories come from the stored relationship records. They are not a claim of automatic discovery.</p>
      </Tile>
      <Tile>
        <h2 className="section-title">Provider discovery</h2>
        <p>{providerDiscoveryText(report.provider_discovery)}</p>
        {report.provider_discovery?.status !== "not_scanned" && report.unresolved?.length ? report.unresolved.slice(0, 10).map((item) => <p key={`${item.service}-${item.name}`}>{item.service}: {item.name || item.host}</p>) : null}
        <p className="helper">These are topology provider references. Unresolved code symbols are not counted here.</p>
      </Tile>
      <Tile>
        <h2 className="section-title">History</h2>
        <p>{report.history?.label || report.intelligence_status.history?.label || "Unavailable"}</p>
        {report.history?.missing?.length ? <p>Missing history: {report.history.missing.join(", ")}</p> : null}
        <p className="helper">{report.history?.note || "Missing history is not the same as zero changes."}</p>
      </Tile>
      <HealthRollup health={report.health} title="Repository health roll-up" />
    </>
  );
}

function RepositorySections({ report }: { report: IntelligenceReport }) {
  const architecture = report.architecture;
  return (
    <>
      <Tile>
        <h2 className="section-title">Repository identity</h2>
        <p>Service {report.identity.service || report.service?.service || "not recorded"}.</p>
        {report.service?.repository ? <p>Repository {report.service.repository}.</p> : null}
        <p className="helper">Graph {report.reproducibility.graph_version || "not recorded"}. {report.service?.graph_label || ""}</p>
      </Tile>
      <Tile>
        <h2 className="section-title">Architecture</h2>
        <p>These metrics describe internal repository structure, not cross-service impact.</p>
        <p>{architecture?.files ?? "—"} files. {architecture?.symbols ?? report.service?.symbols ?? "—"} symbols. {architecture?.internal_relationships ?? "—"} internal relationships.</p>
      </Tile>
      <Tile>
        <h2 className="section-title">Contracts provided</h2>
        {report.contracts?.provided?.length ? report.contracts.provided.map((item) => (
          <p key={item.name}>{item.name} ({item.kind || "contract"}). Consumers: {(item.consumers || []).join(", ") || "none recorded"}.</p>
        )) : <p>No provided contracts are recorded.</p>}
      </Tile>
      <Tile>
        <h2 className="section-title">Contracts consumed</h2>
        {report.contracts?.consumed?.length ? report.contracts.consumed.map((item) => (
          <p key={`${item.provider}-${item.name}`}>{item.name || "Contract"}. Provider: {item.provider || "Provider unresolved"}.</p>
        )) : <p>No consumed contracts are recorded.</p>}
      </Tile>
      <Tile>
        <h2 className="section-title">This service depends on</h2>
        {report.relationships?.upstream?.length ? report.relationships.upstream.map((item) => <p key={`${item.to_service}-${item.contract}`}>{item.to_service} via {item.contract}</p>) : <p>No upstream dependencies are recorded.</p>}
      </Tile>
      <Tile>
        <h2 className="section-title">Services that depend on this service</h2>
        {report.relationships?.downstream?.length ? report.relationships.downstream.map((item) => <p key={`${item.from_service}-${item.contract}`}>{item.from_service} via {item.contract}</p>) : <p>No downstream dependents are recorded.</p>}
      </Tile>
      <Tile>
        <h2 className="section-title">History</h2>
        <p>{report.history?.label || "Unavailable"}</p>
        <p className="helper">{report.history?.note || "Missing history is not the same as zero changes."}</p>
      </Tile>
      <HealthRollup health={report.health} title="Repository health" />
    </>
  );
}

function HealthRollup({ health, title }: { health: IntelligenceReport["health"]; title: string }) {
  const names: Record<string, string> = { packages: "Package inventory", updates: "Dependency hygiene", security: "Security advisories", coverage: "Coverage" };
  return (
    <Tile>
      <h2 className="section-title">{title}</h2>
      {health ? Object.entries(health).map(([key, item]) => (
        <p key={key}>{names[key] || key}: {item.label || item.status || "Unavailable"}. {item.status === "not_scanned" ? "This has not been scanned." : ""}</p>
      )) : <p>Repository health has not been scanned.</p>}
    </Tile>
  );
}
