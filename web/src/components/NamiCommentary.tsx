import { Tile } from "@carbon/react";
import type { ReviewCommentary as Commentary, ReviewReport } from "../api";

export function NamiCommentary({ report }: { report: ReviewReport }) {
  const commentary = report.commentary;
  if (!commentary) return null;
  const failed = report.gate_validation?.status === "failed";
  return (
    <Tile>
      <p className="section-title">Nami explanation</p>
      <h2>{commentary.headline}</h2>
      <p>{commentary.summary}</p>
      <p className="section-title">Why it matters</p>
      <p>{commentary.why_it_matters}</p>
      <p className="section-title">What to do next</p>
      <p>{commentary.next_action}</p>
      {commentary.evidence_comment ? <p className="helper">{commentary.evidence_comment}</p> : null}
      {commentary.validation_comment ? <p className="helper">{commentary.validation_comment}</p> : null}
      {commentary.limitation_note ? <p className="helper">{commentary.limitation_note}</p> : null}
      <p className="helper">Generated from validated Nami evidence</p>
      <p className="helper">{disclosure(commentary, failed)}</p>
    </Tile>
  );
}

function disclosure(commentary: Commentary, failed: boolean): string {
  if (failed) return "Validation failed. Review the technical validation details before relying on this result.";
  if (commentary.mode === "deterministic_fallback" || commentary.source === "deterministic") {
    return "Commentary mode: deterministic fallback";
  }
  const provider = commentary.provider ? `Provider: ${commentary.provider}` : "Generated explanation";
  const model = commentary.model ? ` Model: ${commentary.model}` : "";
  return `Generated explanation. ${provider}.${model}`;
}
