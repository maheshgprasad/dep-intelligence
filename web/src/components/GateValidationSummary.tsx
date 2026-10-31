import { useState } from "react";
import { Button, StructuredListBody, StructuredListCell, StructuredListRow, StructuredListWrapper, Tag, Tile } from "@carbon/react";
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
      <p className="helper">This validates consistency between the stored evidence, configured rules and Gate result. It does not predict the production outcome of the change.</p>
      {status === "validated" ? (
        <p className="helper">
          {invariants.passed} / {invariants.total} safety checks passed
          {counterfactuals.total ? `. ${counterfactuals.passed} / ${counterfactuals.total} counterfactual checks passed` : ""}
        </p>
      ) : null}
      {status === "failed" ? <p>The stored Gate decision was not changed.</p> : null}
      <Button kind="ghost" size="sm" onClick={() => setOpen((value) => !value)}>
        {status === "failed" ? "View technical details" : status === "validated_with_limitations" ? "See limitation" : "View validation details"}
      </Button>
      {open ? <ValidationDetails validation={validation} /> : null}
    </Tile>
  );
}

function lead(validation: GateValidation): string {
  if (validation.status === "failed") {
    return "Nami could not independently reproduce the stored Gate decision. Do not rely on this decision until the validation mismatch is reviewed.";
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
          <p><a href={`#rule-${rule.rule_id}`}>{rule.title}</a></p>
          <p className="helper">{rule.rule_id}</p>
          <StructuredListWrapper>
            <StructuredListBody>
              <StructuredListRow head>
                <StructuredListCell head>Requirement</StructuredListCell>
                <StructuredListCell head>Evidence</StructuredListCell>
                <StructuredListCell head>Result</StructuredListCell>
              </StructuredListRow>
              {rule.requirements.map((item) => (
                <StructuredListRow key={`${rule.rule_id}-${item.requirement}`}>
                  <StructuredListCell>{item.label || item.requirement.replaceAll("_", " ")}</StructuredListCell>
                  <StructuredListCell>{item.evidence_refs.length ? item.evidence_refs.map((id) => <a key={id} href={`#evidence-${id}`}>{id} </a>) : "—"}</StructuredListCell>
                  <StructuredListCell>{resultLabel(item.status)}</StructuredListCell>
                </StructuredListRow>
              ))}
              {rule.evidence_grade ? (
                <StructuredListRow>
                  <StructuredListCell>Minimum evidence grade</StructuredListCell>
                  <StructuredListCell>{rule.evidence_grade}</StructuredListCell>
                  <StructuredListCell>Satisfied</StructuredListCell>
                </StructuredListRow>
              ) : null}
              <StructuredListRow>
                <StructuredListCell>Decisive counter-evidence</StructuredListCell>
                <StructuredListCell>{rule.counter_evidence_status === "decisive_contradiction" ? "Found" : "None"}</StructuredListCell>
                <StructuredListCell>{rule.counter_evidence_status === "decisive_contradiction" ? "Claim requires review" : "Clear"}</StructuredListCell>
              </StructuredListRow>
            </StructuredListBody>
          </StructuredListWrapper>
          <p className="helper">Observed decision {(validation.observed_decision || "").toUpperCase()}. Independent replay {(rule.expected_decision || validation.expected_decision || "").toUpperCase()}. Consistency {rule.status === "validated" ? "Confirmed" : rule.status}.</p>
        </div>
      )) : <p className="helper">No triggered rules.</p>}
      <p className="section-title">Safety invariants</p>
      <ul className="action-list">
        {validation.invariants.items.map((item) => (
          <li key={item.invariant_id}>{item.title} <Tag type={item.status === "passed" ? "green" : item.status === "failed" ? "red" : "gray"}>{statusWord(item.status)}</Tag></li>
        ))}
      </ul>
      <p className="section-title">Counterfactual checks</p>
      {validation.counterfactuals.items.length ? (
        <ul className="action-list">
          {validation.counterfactuals.items.map((item) => (
            <li key={item.counterfactual_id}>
              <strong>{plainChange(item.change)}</strong>
              <p>Expected: {plainOutcome(item.expected)}</p>
              <p>Observed: {plainOutcome(item.observed)}</p>
              <p>Result: {statusWord(item.status)}. <span className="helper">{item.counterfactual_id}</span></p>
            </li>
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

function resultLabel(status: string): string {
  if (status === "satisfied" || status === "passed") return "Confirmed";
  if (status === "missing" || status === "failed") return "Missing";
  return status ? status.charAt(0).toUpperCase() + status.slice(1) : "Not recorded";
}

function statusWord(status: string): string {
  if (status === "passed" || status === "validated") return "Passed";
  if (status === "failed") return "Failed";
  if (status === "not_applicable") return "Not applicable";
  return status ? status.charAt(0).toUpperCase() + status.slice(1) : "Not recorded";
}

function plainChange(value: string): string {
  const text = value.toLowerCase();
  if (text.includes("consumer")) return "Known-consumer dependency removed";
  if (text.includes("verification") || text.includes("exit")) return "Required verification changed to passed";
  if (text.includes("grade") || text.includes("verified")) return "Evidence grade lowered";
  if (text.includes("stale") || text.includes("fresh")) return "Structural evidence marked out of date";
  return value.replaceAll("_", " ");
}

function plainOutcome(value: string): string {
  const text = value.toLowerCase();
  if (text.includes("not_satisfied") || text.includes("rule_not")) return "The rule should stop applying.";
  if (text.includes("satisfied")) return "The rule still applies.";
  return value.replaceAll("_", " ");
}
