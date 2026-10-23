import { symbolName } from "../labels";

export type PathHop = {
  relation?: string;
  source?: string;
  target?: string;
  source_name?: string;
  target_name?: string;
};

export function FocusedImpactPath({ steps }: { steps: PathHop[] }) {
  if (!steps.length) return <p className="helper">No structural path was recorded for this candidate.</p>;
  return (
    <ol className="impact-path">
      {steps.map((step, index) => {
        const from = serviceOf(step.source);
        const to = serviceOf(step.target);
        return (
          <li key={`${step.source}-${step.target}-${index}`}>
            <p className="path-service">{from || "Changed code"}</p>
            <p className="path-symbol">{symbolName(step.source_name || step.source || "")}</p>
            {step.relation ? <p className="path-relation">{relationLabel(step.relation)}</p> : null}
            {from && to && from !== to ? <p className="boundary">repository boundary</p> : null}
            <p className="path-service">{to || "Affected code"}</p>
            <p className="path-symbol">{symbolName(step.target_name || step.target || "")}</p>
          </li>
        );
      })}
    </ol>
  );
}

function relationLabel(relation: string): string {
  return relation.replaceAll("_", " ").toLowerCase();
}

function serviceOf(key?: string): string {
  if (!key || !key.includes(":")) return "";
  const head = key.split(":")[0];
  return head.includes("/") ? "" : head;
}

