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
              <StructuredListCell>{item.label}</StructuredListCell>
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
