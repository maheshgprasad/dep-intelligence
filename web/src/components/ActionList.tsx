import { Tile } from "@carbon/react";
import type { ReviewAction } from "../api";

export function ActionList({ actions }: { actions: ReviewAction[] }) {
  const required = actions.filter((item) => item.required);
  const recommended = actions.filter((item) => !item.required);
  if (!required.length && !recommended.length) return null;
  return (
    <div className="stack-gap">
      {required.length ? (
        <Tile>
          <h2 className="section-title">Required before proceeding</h2>
          <ActionItems items={required} />
        </Tile>
      ) : null}
      {recommended.length ? (
        <Tile>
          <h2 className="section-title">Recommended follow-up</h2>
          <ActionItems items={recommended} />
        </Tile>
      ) : null}
    </div>
  );
}

function ActionItems({ items }: { items: ReviewAction[] }) {
  const groups = new Map<string, ReviewAction[]>();
  const plain: ReviewAction[] = [];
  for (const item of items) {
    if (!item.alternative_group) {
      plain.push(item);
      continue;
    }
    groups.set(item.alternative_group, [...(groups.get(item.alternative_group) ?? []), item]);
  }
  return (
    <ul className="action-list">
      {plain.map((item) => (
        <li key={item.action_id}>
          <strong>{item.title}</strong>
          <p>{item.description}</p>
        </li>
      ))}
      {[...groups.values()].map((group) => (
        <li key={group.map((item) => item.action_id).join("-")}>
          <p>Choose one:</p>
          <ul>
            {group.map((item) => (
              <li key={item.action_id}><strong>{item.title}</strong></li>
            ))}
          </ul>
          <p>{group[0]?.description}</p>
        </li>
      ))}
    </ul>
  );
}
