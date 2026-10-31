import { Information } from "@carbon/icons-react";
import { Accordion, AccordionItem, Tag, Tile, Toggletip, ToggletipButton, ToggletipContent } from "@carbon/react";
import type { DecisionRule, ReviewEvidence } from "../api";
import { EVIDENCE_QUALITY } from "../reviewLabels";

export function DecisionReason({ rules, evidence }: { rules: DecisionRule[]; evidence: ReviewEvidence[] }) {
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

function labelGrade(value: string): string {
  if (!value) return "Not recorded";
  return value.charAt(0).toUpperCase() + value.slice(1);
}
