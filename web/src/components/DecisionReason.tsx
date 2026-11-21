import { Information } from "@carbon/icons-react";
import { Accordion, AccordionItem, Tag, Tile, Toggletip, ToggletipButton, ToggletipContent } from "@carbon/react";
import type { DecisionRule, GateEvidenceSummary, ReviewEvidence } from "../api";
import { EVIDENCE_QUALITY } from "../reviewLabels";

export function DecisionReason({
  summary,
  rules,
  evidence,
}: {
  summary?: GateEvidenceSummary;
  rules: DecisionRule[];
  evidence: ReviewEvidence[];
}) {
  if (summary?.summary) return <GateEvidence summary={summary} />;
  const triggered = rules.filter((rule) => rule.outcome === "triggered");
  if (!triggered.length) return null;
  return (
    <section className="stack-gap">
      <div className="inline-help">
        <h2 className="section-title">Why Nami made this decision</h2>
        <Toggletip align="bottom">
          <ToggletipButton label="What evidence quality means">
            <Information />
          </ToggletipButton>
          <ToggletipContent>
            <p>{EVIDENCE_QUALITY.verified}</p>
            <p>{EVIDENCE_QUALITY.strong}</p>
            <p>{EVIDENCE_QUALITY.moderate}</p>
            <p>{EVIDENCE_QUALITY.weak}</p>
          </ToggletipContent>
        </Toggletip>
      </div>
      {triggered.map((rule) => {
        const supporting = evidence.filter((item) => rule.evidence_refs.includes(item.evidence_id));
        return (
          <Tile key={rule.rule_id} id={`rule-${rule.rule_id}`}>
            <div className="decision-kicker">
              <strong>{rule.title}</strong>
              <Tag type={rule.blocks ? "red" : "warm-gray"}>{rule.blocks ? "Blocking" : "Review"}</Tag>
              <span>Evidence: {labelGrade(rule.evidence_grade)}</span>
            </div>
            <p>{supporting[0]?.statement || "The stored evidence for this rule is listed below."}</p>
            <Accordion>
              <AccordionItem title={`See ${supporting.length} supporting evidence item${supporting.length === 1 ? "" : "s"}`}>
                {supporting.map((item) => (
                  <p key={item.evidence_id}>{item.statement}</p>
                ))}
              </AccordionItem>
              <AccordionItem title="Technical details">
                <p>Rule {rule.rule_id}. Evidence {rule.evidence_refs.join(", ") || "none"}.</p>
              </AccordionItem>
            </Accordion>
          </Tile>
        );
      })}
    </section>
  );
}

function GateEvidence({ summary }: { summary: GateEvidenceSummary }) {
  return (
    <section className="stack-gap">
      <div className="inline-help">
        <h2 className="section-title">{summary.section_title}</h2>
        <Toggletip align="bottom">
          <ToggletipButton label="What evidence quality means">
            <Information />
          </ToggletipButton>
          <ToggletipContent>
            <p>{EVIDENCE_QUALITY.verified}</p>
            <p>{EVIDENCE_QUALITY.strong}</p>
            <p>{EVIDENCE_QUALITY.moderate}</p>
            <p>{EVIDENCE_QUALITY.weak}</p>
          </ToggletipContent>
        </Toggletip>
      </div>
      <Tile>
        <p className="section-title">{summary.heading}</p>
        <p>{summary.summary}</p>
        {summary.items.map((item) => (
          <div key={item.rule_id} id={`rule-${item.rule_id}`}>
            <h3 className="section-title">{item.title}</h3>
            <p className="section-title">Why this triggered the Gate</p>
            <p>{item.why}</p>
            <p>{item.meaning}</p>
            <p className="section-title">Evidence</p>
            {item.evidence.length ? (
              <>
                <p>{item.evidence.map((fact) => fact.evidence_id).join(", ")}</p>
                {item.evidence.slice(0, 2).map((fact) => (
                  <div key={fact.evidence_id}>
                    <p><strong>{fact.supports}</strong></p>
                    <p>{fact.statement}</p>
                    <p className="helper">Source: {fact.source_label || fact.source || "Stored evidence"}</p>
                  </div>
                ))}
                {item.evidence.length > 2 ? <p className="helper">Showing 2 of {item.evidence.length} evidence statements. Every evidence id for this rule is listed above.</p> : null}
              </>
            ) : <p>No linked evidence was stored for this rule.</p>}
            <p className="section-title">{item.action_label}</p>
            <p>{item.action}</p>
            <p className="helper">Decision rule {item.rule_id} · {item.title}</p>
            <Accordion>
              <AccordionItem title="Technical details">
                <p>Rule {item.rule_id}.</p>
                {item.evidence.map((fact) => (
                  <p key={`${fact.evidence_id}-technical`}>
                    {fact.evidence_id}
                    {fact.technical?.file_path ? ` · ${fact.technical.file_path}` : ""}
                    {fact.technical?.line_start ? `:${fact.technical.line_start}` : ""}
                    {fact.technical?.extractor ? ` · Extractor ${fact.technical.extractor}` : ""}
                    {fact.technical?.graph_version ? ` · Graph ${fact.technical.graph_version}` : ""}
                    {fact.technical?.head_sha ? ` · Revision ${String(fact.technical.head_sha).slice(0, 7)}` : ""}
                  </p>
                ))}
              </AccordionItem>
            </Accordion>
          </div>
        ))}
        <p className="section-title">Decision validation</p>
        <p>{summary.validation.statement}</p>
      </Tile>
    </section>
  );
}

function labelGrade(value: string): string {
  if (!value) return "Not recorded";
  return value.charAt(0).toUpperCase() + value.slice(1);
}
