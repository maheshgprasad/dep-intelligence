import { Accordion, AccordionItem, Tile } from "@carbon/react";
import type { ReviewEvidence, SuppressedFinding } from "../api";
import { sourceLabel } from "../reviewLabels";

export function EvidenceList({
  evidence,
  counter,
  suppressed,
}: {
  evidence: ReviewEvidence[];
  counter: ReviewEvidence[];
  suppressed: SuppressedFinding[];
}) {
  if (!evidence.length && !counter.length && !suppressed.length) return null;
  return (
    <section className="stack-gap">
      <h2 className="section-title">Evidence summary</h2>
      {evidence.slice(0, 8).map((item) => (
        <Tile key={item.evidence_id}>
          <p className="section-title">{item.statement}</p>
          <dl className="detail-list">
            <div><dt>Source</dt><dd>{item.source_label || sourceLabel(item.source)}</dd></div>
            <div><dt>Where</dt><dd>{locationText(item)}</dd></div>
            <div><dt>Freshness</dt><dd>{item.freshness === "current" ? "Current" : item.freshness === "stale" ? "Out of date" : item.freshness || "Not recorded"}</dd></div>
            <div><dt>Supports</dt><dd>{plainSupports(item.supports)}</dd></div>
          </dl>
          {item.base_sha || item.head_sha ? <p className="helper">Revision {short(item.base_sha)} → {short(item.head_sha)}</p> : null}
          <Accordion>
            <AccordionItem title="Technical details">
              <p>{item.evidence_id}. Extractor {item.source}. {item.graph_version ? `Graph ${item.graph_version}.` : ""}</p>
            </AccordionItem>
          </Accordion>
        </Tile>
      ))}
      {counter.length ? (
        <Tile>
          <h3 className="section-title">Counter-evidence checked</h3>
          {counter.map((item) => (
            <p key={item.evidence_id}>{item.statement}</p>
          ))}
        </Tile>
      ) : null}
      {suppressed.length ? (
        <Accordion>
          <AccordionItem title="Suppressed findings">
            {suppressed.map((item) => (
              <div key={`${item.finding_id}-${item.reason}`}>
                <p className="section-title">{item.potential_claim}</p>
                <p>Suppressed because: {item.reason}</p>
              </div>
            ))}
          </AccordionItem>
        </Accordion>
      ) : null}
    </section>
  );
}

function locationText(item: ReviewEvidence): string {
  const service = item.service_id || item.repository || "";
  const file = item.file_path || "";
  if (!file) return service || "Not recorded";
  const lines = item.line_start ? (item.line_end && item.line_end !== item.line_start ? `:${item.line_start}–${item.line_end}` : `:${item.line_start}`) : "";
  return `${service ? `${service}/` : ""}${file}${lines}`;
}

function plainSupports(values: string[]): string {
  if (!values.length) return "General review context";
  return values.map((value) => value.replaceAll("_", " ")).join(", ");
}

function short(value: string | null | undefined): string {
  if (!value) return "—";
  return value.slice(0, 7);
}
