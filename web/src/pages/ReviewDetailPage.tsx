import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  Accordion,
  AccordionItem,
  CodeSnippet,
  Column,
  Grid,
  InlineNotification,
  OverflowMenu,
  OverflowMenuItem,
  ProgressBar,
  Tab,
  TabList,
  TabPanel,
  TabPanels,
  Tabs,
  Tile,
} from "@carbon/react";
import { getReview, reviewJob, sendReviewFeedback, type ReviewFinding, type ReviewReport } from "../api";
import { FocusedImpactPath } from "../components/FocusedImpactPath";
import { NamiDataTable } from "../components/NamiDataTable";
import { NamiEmptyState } from "../components/NamiEmptyState";
import { PageHeader } from "../components/PageHeader";
import { StatusTag } from "../components/StatusTag";
import { hideFilePaths, repoSymbol, symbolName } from "../labels";

const STAGES = [
  ["resolve_change", "Resolve change"],
  ["build_change_view", "Build change view"],
  ["align_graph", "Align graph"],
  ["calculate_impact", "Analyze impact"],
  ["apply_policies", "Evaluate policies"],
  ["verify_findings", "Verify findings"],
  ["gate_decision", "Make decision"],
] as const;

const FEEDBACK = [
  ["accepted", "Useful"],
  ["false_positive", "False positive"],
  ["not_applicable", "Not applicable"],
  ["duplicate", "Duplicate"],
  ["fixed", "Fixed"],
  ["acknowledged_risk", "Accepted risk"],
] as const;

export function ReviewDetailPage() {
  const { reviewId = "" } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const isJob = params.get("job") === "1";
  const [report, setReport] = useState<ReviewReport | null>(null);
  const [error, setError] = useState("");
  const [stage, setStage] = useState("resolve_change");
  const [jobStatus, setJobStatus] = useState(isJob ? "running" : "");
  const [note, setNote] = useState("");

  useEffect(() => {
    if (!reviewId) return;
    if (isJob) return;
    void getReview(reviewId)
      .then(setReport)
      .catch((exc: unknown) => setError(exc instanceof Error ? exc.message : "The review could not be loaded."));
  }, [reviewId, isJob]);

  useEffect(() => {
    if (!isJob || !reviewId) return;
    const source = new EventSource("/api/events");
    source.addEventListener("review_progress", (event) => {
      const data = JSON.parse((event as MessageEvent).data) as { stage?: string };
      if (data.stage) setStage(data.stage);
    });
    const timer = window.setInterval(() => {
      void reviewJob(reviewId)
        .then((job) => {
          setJobStatus(job.status);
          if (job.status === "succeeded" && job.result?.review_id) {
            window.clearInterval(timer);
            navigate(`/review/${job.result.review_id}`, { replace: true });
          }
          if (job.status === "failed") {
            window.clearInterval(timer);
            setError(job.error || "The review failed.");
          }
        })
        .catch((exc: unknown) => setError(exc instanceof Error ? exc.message : "The review job was not found."));
    }, 1000);
    return () => {
      source.close();
      window.clearInterval(timer);
    };
  }, [isJob, reviewId, navigate]);

  if (error && !report) {
    return <InlineNotification kind="error" title="Review unavailable" subtitle={error} lowContrast hideCloseButton />;
  }
  if (!report) {
    const index = Math.max(0, STAGES.findIndex(([id]) => id === stage));
    return (
      <div className="stack-gap">
        <PageHeader title="Reviewing change" breadcrumbs={[{ label: "Review change", href: "/review" }, { label: reviewId.slice(0, 8) }]} />
        <ProgressBar label={STAGES[index]?.[1] ?? "Reviewing change"} value={Math.round(((index + 1) / STAGES.length) * 100)} max={100} status={jobStatus === "failed" ? "error" : "active"} />
        <ul className="stage-list">
          {STAGES.map(([id, label], position) => (
            <li key={id}>{label} — {position < index ? "complete" : position === index ? "running" : "waiting"}</li>
          ))}
        </ul>
      </div>
    );
  }

  const decision = report.gate.decision.toLowerCase();
  const kind = decision === "pass" ? "success" : decision === "block" ? "error" : "warning";
  const blocking = new Set(report.gate.blocking_findings);

  return (
    <div className="stack-gap">
      <PageHeader
        title="Review change"
        breadcrumbs={[{ label: "Review change", href: "/review" }, { label: report.review_id.slice(0, 8) }]}
        eyebrow="Powered by Nami Boundary Gate"
      />
      <InlineNotification kind={kind} title={decision.toUpperCase()} subtitle={report.summary.text} lowContrast hideCloseButton />
      {note ? <InlineNotification kind="success" title="Feedback stored" subtitle={note} lowContrast /> : null}
      <Grid>
        <Column sm={4} md={2} lg={3}><Tile><p className="stat-label">Changed files</p><p className="stat-value">{report.changed_files.length}</p></Tile></Column>
        <Column sm={4} md={2} lg={3}><Tile><p className="stat-label">Changed services</p><p className="stat-value">{report.change.service_id ? 1 : 0}</p></Tile></Column>
        <Column sm={4} md={2} lg={3}><Tile><p className="stat-label">Affected services</p><p className="stat-value">{report.impact.affected_services.length}</p></Tile></Column>
        <Column sm={4} md={2} lg={3}><Tile><p className="stat-label">Findings</p><p className="stat-value">{report.findings.length}</p></Tile></Column>
        <Column sm={4} md={4} lg={4}><Tile><p className="stat-label">Verification</p><p className="stat-value">{passed(report)} / {report.verification.length || 0} passed</p></Tile></Column>
      </Grid>
      <Tabs>
        <TabList aria-label="Review sections">
          <Tab>Findings</Tab>
          <Tab>Impact</Tab>
          <Tab>Verification</Tab>
          <Tab>Change</Tab>
        </TabList>
        <TabPanels>
          <TabPanel>
            <NamiDataTable
              columns={[
                { key: "severity", header: "Severity", render: (row) => <StatusTag status={row.severity} /> },
                { key: "title", header: "Finding" },
                { key: "location", header: "Location" },
                { key: "evidence_strength", header: "Evidence", render: (row) => <StatusTag status={row.evidence_strength} /> },
                { key: "status", header: "Status", render: (row) => <StatusTag status={blocking.has(row.finding_id) ? "block" : "info"} label={blocking.has(row.finding_id) ? "Blocking" : "Review"} /> },
              ]}
              rows={report.findings.map((finding) => ({
                ...finding,
                id: finding.finding_id,
                location: repoSymbol(report.change.service_id || "", symbolName(finding.file_path || finding.title)),
              }))}
              emptyTitle="No findings"
              emptyDescription="This change did not produce a review finding."
              renderExpanded={(row) => <FindingDetail finding={row} reviewId={report.review_id} onSaved={setNote} />}
            />
          </TabPanel>
          <TabPanel>
            <NamiDataTable
              columns={[
                { key: "rank", header: "Rank" },
                { key: "code", header: "Affected code" },
                { key: "service_id", header: "Service" },
                { key: "evidence", header: "Evidence", render: (row) => <StatusTag status={evidenceKind(row.evidence_origin)} label={evidenceLabel(row.evidence_origin)} /> },
                { key: "rankScore", header: "Impact rank" },
              ]}
              rows={report.impact.ranked_candidates.map((item, index) => ({
                ...item,
                id: `${item.service_id}-${item.file_path}-${index}`,
                code: repoSymbol(item.service_id, symbolName(item.name || item.file_path)),
                rank: index + 1,
                rankScore: (item.hybrid_score ?? 0).toFixed(3),
              }))}
              emptyTitle="No downstream impact found"
              emptyDescription="No files met the current impact criteria."
              renderExpanded={(row) => (
                <div className="stack-gap">
                  <p>{row.statement || "Ranked from the published structural and historical evidence."}</p>
                  <h3 className="section-title">Strongest structural path</h3>
                  <FocusedImpactPath steps={row.structural?.path ?? []} />
                  <Accordion>
                    <AccordionItem title="Technical scores">
                      <p>NRM {formatScore(row.hybrid_score)} · Structural {formatScore(row.structural_score)} · Temporal {formatScore(row.temporal_score)}</p>
                      <p className="helper">Ranking scores are not probabilities of failure.</p>
                    </AccordionItem>
                  </Accordion>
                </div>
              )}
            />
          </TabPanel>
          <TabPanel>
            <NamiDataTable
              columns={[
                { key: "id", header: "Check" },
                { key: "scope", header: "Scope" },
                { key: "status", header: "Result", render: (row) => <StatusTag status={row.status} /> },
                { key: "duration", header: "Duration" },
              ]}
              rows={report.verification.map((item) => ({
                ...item,
                scope: item.command[0] || "command",
                duration: `${item.duration_ms} ms`,
              }))}
              emptyTitle="No verification checks"
              emptyDescription="Verification was not configured for this review."
              renderExpanded={(row) => (
                <div className="stack-gap">
                  <p>Command: {row.command.join(" ") || "—"}</p>
                  <p>Exit code: {row.exit_code ?? "—"}</p>
                  {row.output ? <CodeSnippet type="multi" feedback="Copied" hideCopyButton={false}>{hideFilePaths(row.output.slice(0, 2000))}</CodeSnippet> : <p className="helper">No command output was retained.</p>}
                </div>
              )}
            />
          </TabPanel>
          <TabPanel>
            {report.changed_files.length === 0 ? (
              <NamiEmptyState title="No changed files" description="The change source did not include a diff." />
            ) : (
              <NamiDataTable
                columns={[
                  { key: "file", header: "Code" },
                  { key: "change_type", header: "Change type" },
                  { key: "additions", header: "+ additions" },
                  { key: "deletions", header: "- deletions" },
                ]}
                rows={report.changed_files.map((file, index) => ({
                  id: `${file.new_path || file.old_path}-${index}`,
                  file: repoSymbol(report.change.service_id || "", symbolName(file.new_path || file.old_path || "")),
                  change_type: file.change_type,
                  additions: file.additions ?? 0,
                  deletions: file.deletions ?? 0,
                  path: file.new_path || file.old_path || "",
                }))}
                renderExpanded={(row) => (
                  <div>
                    <p>Mapped symbols: {report.changed_symbols.filter((symbol) => symbol.file_path === row.path).map((symbol) => repoSymbol(report.change.service_id || "", symbolName(symbol.name || symbol.qualified_name))).join(", ") || "—"}</p>
                    <p>Contract changes: {report.contract_changes.filter((item) => item.file_path === row.path).map((item) => hideFilePaths(item.message)).join(" ") || "None recorded."}</p>
                  </div>
                )}
              />
            )}
          </TabPanel>
        </TabPanels>
      </Tabs>
    </div>
  );
}

function FindingDetail({ finding, reviewId, onSaved }: { finding: ReviewFinding & { id: string }; reviewId: string; onSaved: (message: string) => void }) {
  const assisted = finding.evidence?.some((item) => item.source === "llm" || item.source === "model");
  return (
    <div className="stack-gap">
      <p><strong>Claim. </strong>{hideFilePaths(finding.claim)}</p>
      <p><strong>Why this matters. </strong>{finding.title}</p>
      {assisted ? (
        <p>
          <StatusTag status="model" label="AI-assisted" /> A model suggested this concern. Nami's Evidence Gate independently validated the evidence shown below.
        </p>
      ) : null}
      <Accordion>
        <AccordionItem title="Evidence">
          {(finding.evidence ?? []).map((item) => (
            <p key={item.summary}>{item.summary}</p>
          ))}
        </AccordionItem>
        <AccordionItem title="Verification output">
          <p>{finding.verification_status || "Not verified by a command."}</p>
        </AccordionItem>
        <AccordionItem title="Technical details">
          <p>Category {finding.category}. Policy {finding.policy_id || "none"}. Fingerprint {finding.fingerprint || finding.finding_id}.</p>
        </AccordionItem>
      </Accordion>
      <OverflowMenu aria-label="Finding feedback" flipped>
        {FEEDBACK.map(([status, label]) => (
          <OverflowMenuItem
            key={status}
            itemText={label}
            onClick={() => {
              void sendReviewFeedback(reviewId, finding.fingerprint || finding.finding_id, status)
                .then(() => onSaved(`${label} was recorded. Ranking scores were not changed.`))
                .catch((exc: unknown) => onSaved(exc instanceof Error ? exc.message : "Feedback was not stored."));
            }}
          />
        ))}
      </OverflowMenu>
    </div>
  );
}

function passed(report: ReviewReport): number {
  return report.verification.filter((item) => item.status === "passed").length;
}

function evidenceKind(origin: string): string {
  if (origin === "hybrid" || origin === "both") return "both";
  if (origin === "temporal_only" || origin === "temporal") return "history";
  return "structure";
}

function evidenceLabel(origin: string): string {
  if (origin === "hybrid" || origin === "both") return "Structure + history";
  if (origin === "temporal_only" || origin === "temporal") return "History";
  return "Structure";
}

function formatScore(value: number | undefined): string {
  return typeof value === "number" ? value.toFixed(3) : "—";
}
