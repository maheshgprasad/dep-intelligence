import { useState } from "react";
import { CheckmarkFilled, ErrorFilled, Incomplete, WarningAltFilled } from "@carbon/icons-react";
import { Button, Tag, Tile } from "@carbon/react";
import type { ReviewReport } from "../api";
import { blockerCount, primaryTarget, serviceGroups, TARGET_PREVIEW } from "../reviewPresentation";
import { GateOutcomeBanner } from "./GateOutcomeBanner";
import { decisionCopy } from "../reviewLabels";

const ICONS = {
  pass: CheckmarkFilled,
  warn: WarningAltFilled,
  block: ErrorFilled,
  incomplete: Incomplete,
};

export function AssessmentSummary({ report }: { report: ReviewReport }) {
  const decision = report.gate.decision.toLowerCase();
  const copy = report.decision_presentation ?? decisionCopy(decision);
  const Icon = ICONS[decision as keyof typeof ICONS] ?? Incomplete;
  const assessment = report.assessment;
  const enforcement = report.enforcement;
  const actions = report.actions ?? [];
  const required = actions.some((item) => item.required);
  const next = required
    ? (actions.find((item) => item.required)?.description || assessment?.recommended_next_step || "Resolve the required action below.")
    : assessment?.recommended_next_step || copy.note;
  const cluster = report.review_scope?.mode === "service_cluster";
  const blockers = blockerCount(report);
  const validation = report.gate_validation?.status;
  return (
    <Tile className="decision-hero">
      <div className="decision-kicker">
        <Icon size={20} />
        <Tag type={decision === "pass" ? "green" : decision === "block" ? "red" : decision === "incomplete" ? "gray" : "warm-gray"}>{copy.label}</Tag>
        <span>{copy.heading}</span>
      </div>
      <h2>{copy.heading}</h2>
      {cluster ? <ClusterFacts report={report} /> : <p>Review scope: Individual repository. {report.change.service_id ? `Repository ${report.change.service_id}.` : ""}</p>}
      <p>{assessment?.summary || copy.note}</p>
      <p className="section-title">{required ? "Required" : "Recommended next step"}</p>
      <p>{next}</p>
      <p className="helper">Verified blockers: {blockers === 0 ? "None" : blockers}</p>
      {validation ? <p className="helper">Decision validation: {validationLabel(validation)}</p> : null}
      {validation === "failed" ? <p>Independent replay did not reproduce this Gate decision. Do not rely on it until the mismatch is reviewed.</p> : null}
      {enforcement ? <p className="helper">{enforcementText(enforcement.enforcement_mode, enforcement.workflow_prevented)}</p> : null}
      <GateOutcomeBanner />
    </Tile>
  );
}

function ClusterFacts({ report }: { report: ReviewReport }) {
  const [showTargets, setShowTargets] = useState(false);
  const [showQuiet, setShowQuiet] = useState(false);
  const scope = report.review_scope;
  const groups = serviceGroups(report);
  const primary = primaryTarget(report);
  const visible = showTargets ? groups : groups.slice(0, TARGET_PREVIEW);
  const hiddenTargets = Math.max(groups.length - TARGET_PREVIEW, 0);
  const quiet = scope?.unaffected_member_services ?? [];
  const outside = scope?.out_of_scope_targets ?? [];
  const membership = scope?.membership_source === "declared" ? "declared" : "connected";
  return (
    <dl className="detail-list">
      <div><dt>Scope</dt><dd>{scope?.cluster_label || "Service cluster"} · {scope?.member_count ?? 0} {membership} services</dd></div>
      <div><dt>Change origin</dt><dd>{scope?.origin_service_id || report.change.service_id || "—"}</dd></div>
      <div><dt>Evidenced impact</dt><dd>{scope?.affected_member_count ?? 0} of {scope?.member_count ?? 0} services</dd></div>
      <div><dt>Primary review target</dt><dd>{primary || "None recorded"}</dd></div>
      {visible.filter((item) => item.serviceId !== primary).map((item) => (
        <div key={item.serviceId}><dt>{item.direct ? "Direct target" : "Downstream target"}</dt><dd>{item.serviceId}</dd></div>
      ))}
      {hiddenTargets > 0 ? (
        <div>
          <dt>More targets</dt>
          <dd>
            {hiddenTargets} additional evidenced targets.
            <Button kind="ghost" size="sm" onClick={() => setShowTargets((value) => !value)}>{showTargets ? "Show fewer" : "Show all"}</Button>
          </dd>
        </div>
      ) : null}
      <div>
        <dt>No evidenced impact found</dt>
        <dd>
          {scope?.unaffected_member_count ?? quiet.length} services
          {quiet.length ? <Button kind="ghost" size="sm" onClick={() => setShowQuiet((value) => !value)}>{showQuiet ? "Hide services" : "Show services"}</Button> : null}
          {showQuiet ? <span>{quiet.join(", ")}</span> : null}
        </dd>
      </div>
      {outside.length ? (
        <div>
          <dt>Outside this cluster</dt>
          <dd>{outside.length === 1 ? "1 evidenced target exists outside the selected Service Cluster." : `${outside.length} evidenced targets exist outside the selected Service Cluster.`} Run an Individual Repository review if broader cross-cluster impact must be evaluated.</dd>
        </div>
      ) : null}
    </dl>
  );
}

function validationLabel(status: string): string {
  if (status === "validated") return "Validated";
  if (status === "validated_with_limitations") return "Validated with limitations";
  if (status === "failed") return "Validation failed";
  return status;
}

function enforcementText(mode: string, prevented: boolean): string {
  const name = mode.replaceAll("_", " ");
  if (prevented) {
    return `Enforcement: ${name}. This change cannot proceed through the configured workflow until the required action is resolved.`;
  }
  return `Enforcement: ${name}. The workflow is not automatically prevented.`;
}
