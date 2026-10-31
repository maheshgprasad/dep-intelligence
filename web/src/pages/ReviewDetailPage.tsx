import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  Button,
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
import { cancelJob, getReview, reviewJob, sendReviewFeedback, type ReviewFinding, type ReviewReport } from "../api";
import { ActionList } from "../components/ActionList";
import { AssessmentSummary } from "../components/AssessmentSummary";
import { DecisionReason } from "../components/DecisionReason";
import { EvidenceList } from "../components/EvidenceList";
import { ExportMenu } from "../components/ExportMenu";
import { GateChecklist } from "../components/GateChecklist";
import { GateValidationSummary } from "../components/GateValidationSummary";
import { NamiCommentary } from "../components/NamiCommentary";
import { FocusedImpactPath } from "../components/FocusedImpactPath";
import { NamiDataTable } from "../components/NamiDataTable";
import { NamiEmptyState } from "../components/NamiEmptyState";
import { PageHeader } from "../components/PageHeader";
import { StatusTag } from "../components/StatusTag";
import { hideFilePaths, repoSymbol, symbolName } from "../labels";


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
  const [jobStatus, setJobStatus] = useState(isJob ? "running" : "");
  const [feedbackSuccess, setFeedbackSuccess] = useState("");
  const [feedbackError, setFeedbackError] = useState("");
  const [feedbackBusy, setFeedbackBusy] = useState(false);
  const [progress, setProgress] = useState({ stage: "resolve_change", label: "Selecting the change", percent: 11, index: 1, count: 9 });

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
      const data = JSON.parse((event as MessageEvent).data) as { stage?: string; stage_label?: string; percent?: number; stage_index?: number; stage_count?: number; job_id?: string; review_id?: string };
      if (data.job_id !== reviewId && data.review_id !== reviewId) return;
      if (data.stage) {
        setProgress({
          stage: data.stage,
          label: data.stage_label || data.stage,
          percent: data.percent ?? 0,
          index: data.stage_index ?? 1,
          count: data.stage_count ?? 9,
        });
      }
    });
    source.addEventListener("review_completed", (event) => {
      const data = JSON.parse((event as MessageEvent).data) as { review_id?: string; job_id?: string };
      if (data.job_id !== reviewId && data.review_id !== reviewId) return;
      void reviewJob(reviewId).then((job) => {
        if (job.result?.review_id) navigate(`/review/${job.result.review_id}`, { replace: true });
      });
    });
    const timer = window.setInterval(() => {
      void reviewJob(reviewId)
        .then((job) => {
          setJobStatus(job.status);
          if (typeof job.result === "object" && job.result && "review_id" in job.result && job.status === "succeeded") {
            window.clearInterval(timer);
            navigate(`/review/${job.result.review_id}`, { replace: true });
          }
          if (job.status === "cancelled") {
            window.clearInterval(timer);
            setError("Review cancelled");
          }
          if (job.status === "failed") {
            window.clearInterval(timer);
            setError(job.error || "The review failed.");
          }
        })
        .catch((exc: unknown) => setError(exc instanceof Error ? exc.message : "The review job was not found."));
    }, 4000);
    return () => {
      source.close();
      window.clearInterval(timer);
    };
  }, [isJob, reviewId, navigate]);

  if (error === "Review cancelled" && !report) {
    return (
      <div className="stack-gap">
        <InlineNotification kind="info" title="Review cancelled" subtitle="This review stopped before a policy decision." lowContrast hideCloseButton />
        <Button kind="tertiary" onClick={() => navigate("/review")}>Review another change</Button>
      </div>
    );
  }
  if (error && !report) {
    return <InlineNotification kind="error" title="Review unavailable" subtitle={error} lowContrast hideCloseButton />;
  }
  if (!report) {
    return (
      <div className="stack-gap">
        <PageHeader
          title="Reviewing change"
          breadcrumbs={[{ label: "Review change", href: "/review" }, { label: reviewId.slice(0, 8) }]}
          actions={jobStatus === "queued" || jobStatus === "running" ? <Button kind="danger--tertiary" onClick={() => void cancelJob(reviewId).then(() => setJobStatus("cancelled"))}>Cancel review</Button> : null}
        />
        <ProgressBar label={progress.label} value={progress.percent} max={100} status={jobStatus === "failed" ? "error" : "active"} />
        <p className="helper">{progress.label} · {progress.index} of {progress.count}</p>
      </div>
    );
  }

  const decision = report.gate.decision.toLowerCase();
  const kind = decision === "pass" ? "success" : decision === "block" ? "error" : "warning";

  return (
    <div className="stack-gap">
      <PageHeader
        title="Review change"
        breadcrumbs={[{ label: "Review change", href: "/review" }, { label: report.review_id.slice(0, 8) }]}
        eyebrow="Powered by Nami Boundary Gate"
        actions={
          <div className="button-row">
            <Button kind="tertiary" size="sm" onClick={() => navigate("/review")}>Review another change</Button>
            <ExportMenu reviewId={report.review_id} summary={report.copy_summary || report.summary.text} />
          </div>
        }
      />
      <AssessmentSummary report={report} />
      <NamiCommentary report={report} />
      <ActionList actions={report.actions ?? []} />
      <GateValidationSummary validation={report.gate_validation} />
      <DecisionReason rules={report.gate.decision_rules ?? []} evidence={report.evidence_catalog ?? []} />
      <GateChecklist items={report.gate_checklist ?? []} />
      <Tile>
        <p className="section-title">Affected services</p>
        <p>{report.impact.affected_services.length ? report.impact.affected_services.join(", ") : "No other service was identified from the stored evidence."}</p>
        {report.impact.affected_services[0] ? <Link to={`/architecture?service=${encodeURIComponent(report.impact.affected_services[0])}`}>Open architecture</Link> : null}
        {" "}
        {report.impact.affected_services[0] ? <Link to={`/impact?service=${encodeURIComponent(report.impact.affected_services[0])}`}>Explore impact</Link> : null}
      </Tile>
      <EvidenceList evidence={report.evidence_catalog ?? []} counter={report.counter_evidence ?? []} suppressed={report.suppressed_findings ?? []} />
      <InlineNotification kind={report.gate.reason_codes?.includes("cancelled") ? "info" : kind} title={report.gate.reason_codes?.includes("cancelled") ? "Review cancelled" : decision.toUpperCase()} subtitle={reviewExplanation(report)} lowContrast hideCloseButton />
      <dl className="detail-list">
        <div><dt>Review mode</dt><dd>{modeText(report)}</dd></div>
        <div><dt>Graph alignment</dt><dd>{alignmentText(report.change.graph_alignment)}</dd></div>
        <div><dt>Change</dt><dd>{sourceText(report.change.source?.kind)}</dd></div>
      </dl>
      {report.diagnostics.filter((item) => item.severity !== "error").map((item) => (
        <p key={item.code} className="helper">{hideFilePaths(item.message)}</p>
      ))}
      {feedbackSuccess ? <InlineNotification kind="success" title="Success" subtitle={feedbackSuccess} lowContrast hideCloseButton /> : null}
      {feedbackError ? <InlineNotification kind="error" title="Error" subtitle={feedbackError} lowContrast hideCloseButton /> : null}
      <Grid>
        <Column sm={4} md={2} lg={3}><Tile><p className="stat-label">Changed files</p><p className="stat-value">{report.changed_files.length}</p></Tile></Column>
        <Column sm={4} md={2} lg={3}><Tile><p className="stat-label">Affected services</p><p className="stat-value">{report.impact.affected_services.length}</p></Tile></Column>
        <Column sm={4} md={2} lg={3}><Tile><p className="stat-label">Findings</p><p className="stat-value">{report.findings.length}</p></Tile></Column>
        <Column sm={4} md={2} lg={3}><Tile><p className="stat-label">Blocking findings</p><p className="stat-value">{report.gate.blocking_findings.length}</p></Tile></Column>
        <Column sm={4} md={4} lg={4}><Tile><p className="stat-label">Verification</p><p className="stat-value">{report.verification.length ? `${passed(report)} / ${report.verification.length} passed` : "Not run"}</p></Tile></Column>
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
                { key: "status", header: "Status", render: (row) => <StatusTag status={findingTone(row.finding_id, report)} label={findingClass(row.finding_id, report)} /> },
              ]}
              rows={report.findings.map((finding) => ({
                ...finding,
                id: finding.finding_id,
                location: repoSymbol(report.change.service_id || "", symbolName(finding.file_path || finding.title)),
              }))}
              emptyTitle="No findings"
              emptyDescription={report.changed_files.length ? "This change did not produce a review finding." : "This source had no diff, so no finding was produced."}
              renderExpanded={(row) => (
                <FindingDetail
                  finding={row}
                  reviewId={report.review_id}
                  busy={feedbackBusy}
                  onSuccess={(message) => { setFeedbackError(""); setFeedbackSuccess(message); }}
                  onError={(message) => { setFeedbackSuccess(""); setFeedbackError(message); }}
                  onBusy={setFeedbackBusy}
                />
              )}
            />
          </TabPanel>
          <TabPanel>
            <p className="helper">Nami Rank orders downstream files using current dependency structure and historical change evidence. It does not decide whether this change is blocked.</p>
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
                  <div className="button-row">
                    <Link to={`/impact?service=${encodeURIComponent(row.service_id)}&file=${encodeURIComponent(row.file_path)}`}>Open in Impact explorer</Link>
                    <Link to={`/architecture?service=${encodeURIComponent(row.service_id)}&target=${encodeURIComponent(row.file_path)}`}>Open in Architecture</Link>
                  </div>
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

function FindingDetail({ finding, reviewId, busy, onSuccess, onError, onBusy }: { finding: ReviewFinding & { id: string }; reviewId: string; busy: boolean; onSuccess: (message: string) => void; onError: (message: string) => void; onBusy: (value: boolean) => void }) {
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
            disabled={busy}
            onClick={() => {
              if (busy) return;
              onBusy(true);
              void sendReviewFeedback(reviewId, finding.fingerprint || finding.finding_id, status)
                .then(() => onSuccess(status === "false_positive" ? "False positive was recorded." : `${label} was recorded.`))
                .catch(() => onError("Feedback was not stored."))
                .finally(() => onBusy(false));
            }}
          />
        ))}
      </OverflowMenu>
    </div>
  );
}

function findingClass(id: string, report: ReviewReport): string {
  if (report.gate.blocking_findings.includes(id)) return "Blocking";
  if (report.gate.warning_findings.includes(id)) return "Warning";
  if (report.gate.review_targets.includes(id)) return "Review target";
  return "Advisory";
}

function findingTone(id: string, report: ReviewReport): string {
  if (report.gate.blocking_findings.includes(id)) return "block";
  if (report.gate.warning_findings.includes(id)) return "warn";
  return "info";
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

function reviewExplanation(report: ReviewReport): string {
  if (report.gate.reason_codes?.includes("cancelled")) return "This review stopped before a policy decision.";
  const errors = report.diagnostics.filter((item) => item.severity === "error").map((item) => hideFilePaths(item.message)).filter(Boolean);
  const mode = modeText(report);
  const alignment = alignmentText(report.change.graph_alignment);
  if (errors.length) return `${mode} ${alignment} ${errors.join(" ")}`;
  if (report.changed_files.length === 0) return `${mode} ${alignment} This source had no diff, so nothing was ranked and no finding was produced.`;
  return `${mode} ${alignment}`;
}

function modeText(report: ReviewReport): string {
  const requested = knownMode(report.requested_mode) || knownMode(report.mode);
  const resolved = knownMode(report.resolved_mode) || knownMode(report.mode);
  if (!requested && !resolved) return "Review mode was not recorded.";
  if (requested === "auto" && resolved === "fast") return "Recommended ran as Fast and used the published intelligence snapshot.";
  if (requested === "auto" && resolved === "exact") return "Recommended ran as Exact and built a private graph for this revision.";
  if (resolved === "exact" || requested === "exact") return "Exact built a private graph for this revision.";
  if (resolved === "fast" || requested === "fast") return "Fast used the published intelligence snapshot.";
  return "Recommended.";
}

function knownMode(value: string | undefined): string {
  return value === "auto" || value === "fast" || value === "exact" ? value : "";
}

function alignmentText(alignment: string | undefined): string {
  if (alignment === "exact") return "The published graph matches this revision.";
  if (alignment === "compatible") return "The published graph is aligned with this revision.";
  if (alignment === "stale") return "The published graph is not this revision.";
  return "No published graph was available for this revision.";
}

function sourceText(kind: string | undefined): string {
  const labels: Record<string, string> = {
    working_tree: "Working tree",
    staged: "Staged changes",
    commit: "Commit",
    range: "Commit range",
    github_pr: "Pull request",
    explicit: "Selected files",
  };
  return labels[kind || ""] || "Change";
}
