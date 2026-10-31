import { useState } from "react";
import { Button, Tag, Tile } from "@carbon/react";
import type { GateValidation } from "../api";
import { validationHelp, validationLabel } from "../reviewLabels";

export function GateValidationSummary({ validation }: { validation?: GateValidation }) {
  const [open, setOpen] = useState(false);
  if (!validation) return null;
  const status = validation.status;
  const tone = status === "validated" ? "green" : status === "failed" ? "red" : "gray";
  const invariants = validation.invariants;
  const counterfactuals = validation.counterfactuals;
  return (
    <Tile>
      <p className="section-title">Decision logic validation</p>
      <div className="decision-kicker">
        <Tag type={tone} title={validationHelp(status)}>{validationLabel(status)}</Tag>
      </div>
      <p>{lead(validation)}</p>
      {status === "validated" ? (
        <p className="helper">
          {invariants.passed} / {invariants.total} safety checks passed
          {counterfactuals.total ? `. ${counterfactuals.passed} / ${counterfactuals.total} counterfactual checks passed` : ""}
        </p>
      ) : null}
      {status === "failed" ? <p>Do not rely on this decision until the validation issue is resolved.</p> : null}
      <Button kind="ghost" size="sm" onClick={() => setOpen((value) => !value)}>
        {status === "failed" ? "View technical details" : status === "validated_with_limitations" ? "See limitation" : "View validation details"}
      </Button>
      {open ? <ValidationDetails validation={validation} /> : null}
    </Tile>
  );
}

function lead(validation: GateValidation): string {
  if (validation.status === "failed") {
    return "Nami could not independently reproduce the stored Gate decision.";
  }
  if (validation.status === "validated_with_limitations") {
    return validation.summary || "The Gate decision was reproduced, but one optional provenance check could not be completed.";
  }
  const label = validation.observed_decision.toUpperCase();
  return `Nami independently replayed the configured Gate rules using the stored evidence and reproduced the same ${label} decision. This checks evidence, rules, and the Gate result. It does not prove the production outcome.`;
}

function ValidationDetails({ validation }: { validation: GateValidation }) {
  const fingerprint = validation.reproducibility;
  return (
    <div className="stack-gap">
      <dl className="detail-list">
        <div><dt>Observed decision</dt><dd>{validation.observed_decision.toUpperCase()}</dd></div>
        <div><dt>Expected decision</dt><dd>{validation.expected_decision.toUpperCase()}</dd></div>
        <div><dt>Validator version</dt><dd>{validation.validator_version}</dd></div>
        <div><dt>Gate input fingerprint</dt><dd>{fingerprint.gate_input_hash}</dd></div>
      </dl>
      <p className="helper">Integrity fingerprint, not digital signature.</p>
      <p className="section-title">Triggered rules</p>
      {validation.rule_validation.length ? validation.rule_validation.map((rule) => (
        <div key={rule.rule_id}>
          <p>{rule.rule_id} {rule.title}</p>
          <ul className="action-list">
            {rule.requirements.map((item) => (
              <li key={`${rule.rule_id}-${item.requirement}`}>{item.label || item.requirement}: {item.status}{item.evidence_refs.length ? ` (${item.evidence_refs.join(", ")})` : ""}</li>
            ))}
            {rule.evidence_grade ? <li>Minimum evidence grade: {rule.evidence_grade}</li> : null}
            <li>Counter-evidence: {rule.counter_evidence_status === "decisive_contradiction" ? "Decisive contradiction" : "None decisive"}</li>
            <li>Expected decision: {(rule.expected_decision || "").toUpperCase()}. Rule check: {rule.status}.</li>
          </ul>
        </div>
      )) : <p className="helper">No triggered rules.</p>}
      <p className="section-title">Safety invariants</p>
      <ul className="action-list">
        {validation.invariants.items.map((item) => (
          <li key={item.invariant_id}>{item.invariant_id} {item.title}: {item.status}</li>
        ))}
      </ul>
      <p className="section-title">Counterfactual checks</p>
      {validation.counterfactuals.items.length ? (
        <ul className="action-list">
          {validation.counterfactuals.items.map((item) => (
            <li key={item.counterfactual_id}>{item.counterfactual_id} {item.rule_id}: {item.change}. Expected {item.expected}. Observed {item.observed}.</li>
          ))}
        </ul>
      ) : <p className="helper">No counterfactual checks applied.</p>}
      {validation.limitations.length ? (
        <>
          <p className="section-title">Limitations</p>
          <ul className="action-list">
            {validation.limitations.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </>
      ) : null}
    </div>
  );
}
