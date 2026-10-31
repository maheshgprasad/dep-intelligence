import { CheckmarkFilled, ErrorFilled, Incomplete, WarningAltFilled } from "@carbon/icons-react";
import { Tag, Tile } from "@carbon/react";
import type { ReviewReport } from "../api";
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
  const next = actions.length
    ? required
      ? "Resolve the required action below."
      : "See the recommended follow-up below."
    : assessment?.recommended_next_step || copy.note;
  return (
    <Tile className="decision-hero">
      <div className="decision-kicker">
        <Icon size={20} />
        <Tag type={decision === "pass" ? "green" : decision === "block" ? "red" : decision === "incomplete" ? "gray" : "warm-gray"}>{copy.label}</Tag>
        <span>{copy.heading}</span>
      </div>
      <h2>{copy.heading}</h2>
      <p>{assessment?.summary || copy.note}</p>
      <p className="section-title">{required ? "Next step" : "Recommended next step"}</p>
      <p>{next}</p>
      {enforcement ? <p className="helper">{enforcementText(enforcement.enforcement_mode, enforcement.workflow_prevented)}</p> : null}
    </Tile>
  );
}

function enforcementText(mode: string, prevented: boolean): string {
  const name = mode.replaceAll("_", " ");
  if (prevented) {
    return `Enforcement: ${name}. This change cannot proceed through the configured workflow until the required action is resolved.`;
  }
  return `Enforcement: ${name}. The workflow is not automatically prevented.`;
}
