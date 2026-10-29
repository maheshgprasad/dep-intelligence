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
  return (
    <Tile className="decision-hero">
      <div className="decision-kicker">
        <Icon size={20} />
        <Tag type={decision === "pass" ? "green" : decision === "block" ? "red" : decision === "incomplete" ? "gray" : "warm-gray"}>{copy.label}</Tag>
        <span>{copy.heading}</span>
      </div>
      <h2>{assessment?.headline || copy.heading}</h2>
      <p>{assessment?.summary || copy.note}</p>
      <p className="section-title">Recommended next step</p>
      <p>{assessment?.recommended_next_step || copy.note}</p>
      {enforcement ? (
        <p className="helper">
          Gate decision {enforcement.gate_decision.toUpperCase()}. Enforcement {enforcement.enforcement_mode}. Workflow prevented: {enforcement.workflow_prevented ? "Yes" : "No"}.
        </p>
      ) : null}
    </Tile>
  );
}
