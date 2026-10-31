import { Accordion, AccordionItem, Tile } from "@carbon/react";
import type { ReviewCommentary as Commentary, ReviewReport } from "../api";

export function NamiCommentary({ report }: { report: ReviewReport }) {
  const commentary = report.commentary;
  if (!commentary) return null;
  const status = report.gate_validation?.status;
  const hasActions = (report.actions ?? []).length > 0;
  return (
    <Tile>
      <p className="section-title">Nami explanation</p>
      <h2>{commentary.headline}</h2>
      <p>{commentary.summary}</p>
      <p className="section-title">Why it matters</p>
      <p>{commentary.why_it_matters}</p>
      {hasActions ? <p>See the required or recommended action below.</p> : (
        <>
          <p className="section-title">What to do next</p>
          <p>{commentary.next_action}</p>
        </>
      )}
      {commentary.evidence_comment ? <p className="helper">{commentary.evidence_comment}</p> : null}
      {commentary.validation_comment ? <p className="helper">{commentary.validation_comment}</p> : null}
      {commentary.limitation_note ? <p className="helper">{commentary.limitation_note}</p> : null}
      <p className="helper">{evidenceLine(status)}</p>
      <Accordion>
        <AccordionItem title="Technical details">
          <p>{modeLine(commentary)}</p>
        </AccordionItem>
      </Accordion>
    </Tile>
  );
}

function evidenceLine(status: string | undefined): string {
  if (status === "validated") return "Based on validated Nami evidence";
  if (status === "validated_with_limitations") return "Based on Nami evidence; decision validation has limitations";
  if (status === "failed") return "Generated from stored Nami evidence; decision validation failed";
  return "Generated from stored Nami evidence";
}

function modeLine(commentary: Commentary): string {
  if (commentary.mode === "deterministic_fallback" || commentary.source === "deterministic") {
    return "Commentary mode: deterministic fallback";
  }
  const provider = commentary.provider ? ` Provider: ${commentary.provider}.` : "";
  const model = commentary.model ? ` Model: ${commentary.model}.` : "";
  return `Commentary mode: LLM.${provider}${model}`;
}
