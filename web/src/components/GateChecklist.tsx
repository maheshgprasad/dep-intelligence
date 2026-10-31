import { StructuredListBody, StructuredListCell, StructuredListRow, StructuredListWrapper, Tile } from "@carbon/react";
import type { GateChecklistItem } from "../api";
import { StatusTag } from "./StatusTag";
import { CHECK_STATUS } from "../reviewLabels";

export function GateChecklist({ items }: { items: GateChecklistItem[] }) {
  if (!items.length) return null;
  return (
    <Tile>
      <h2 className="section-title">Gate checklist</h2>
      <StructuredListWrapper>
        <StructuredListBody>
          {items.map((item) => (
            <StructuredListRow key={item.check_id}>
              <StructuredListCell>
                <div>{item.label}</div>
                <p className="helper">{checkNote(item.check_id, item.status)}</p>
              </StructuredListCell>
              <StructuredListCell>
                <StatusTag status={item.status === "passed" ? "passed" : item.status === "failed" ? "failed" : item.status === "unavailable" ? "unavailable" : "info"} label={CHECK_STATUS[item.status] || item.status} />
              </StructuredListCell>
            </StructuredListRow>
          ))}
        </StructuredListBody>
      </StructuredListWrapper>
    </Tile>
  );
}

function checkNote(id: string, status: string): string {
  const notes: Record<string, Record<string, string>> = {
    "CHECK-GRAPH": {
      passed: "The published graph matches the reviewed revision.",
      failed: "Structural evidence is out of date or unavailable.",
      unavailable: "Structural evidence is unavailable for this revision.",
    },
    "CHECK-CONTRACT": {
      passed: "No verified consumed contract break was found.",
      failed: "A consumed operation or contract no longer matches its consumers.",
    },
    "CHECK-VERIFY": {
      passed: "Configured required checks completed successfully.",
      failed: "A required verification command failed.",
      not_evaluated: "No required verification was configured for this change.",
      unavailable: "A required verification command did not finish.",
    },
    "CHECK-POLICY": {
      passed: "No blocking project-rule match was found.",
      failed: "A project rule matched this change.",
      not_evaluated: "No project rule was evaluated.",
    },
  };
  return notes[id]?.[status] || "";
}
