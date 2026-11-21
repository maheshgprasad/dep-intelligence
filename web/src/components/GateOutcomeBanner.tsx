import { useState } from "react";
import { Button } from "@carbon/react";

const STORAGE_KEY = "nami-gate-outcome-banner";

const OUTCOMES = [
  ["Pass", "The review finished with no blocking finding, no warning, and no review target. The graph alignment was current."],
  ["Warn", "Nothing blocks. The review still has a warning, a review target, or a stale graph while strict graph mode is off."],
  ["Block", "A verified finding matches a block rule: a consumed contract was removed and a known consumer still calls it, a required check failed, a blocking project rule matched a changed line, or a verified finding is critical."],
  ["Incomplete", "The gate stopped before a decision. The policy is invalid, the repository is not allowlisted, a required check did not finish, or strict graph mode is on and the graph is stale or missing."],
] as const;

export function GateOutcomeBanner() {
  const [hidden, setHidden] = useState(() => window.localStorage.getItem(STORAGE_KEY) === "hidden");

  function hide() {
    window.localStorage.setItem(STORAGE_KEY, "hidden");
    setHidden(true);
  }

  function show() {
    window.localStorage.removeItem(STORAGE_KEY);
    setHidden(false);
  }

  if (hidden) {
    return <Button kind="ghost" size="sm" onClick={show}>Show what Pass, Warn, Block, and Incomplete mean</Button>;
  }

  return (
    <aside className="outcome-banner" aria-label="Boundary Gate outcomes">
      <div className="button-row">
        <p className="eyebrow">Boundary Gate outcomes</p>
        <Button kind="ghost" size="sm" onClick={hide}>Hide</Button>
      </div>
      <dl>
        {OUTCOMES.map(([term, text]) => (
          <div key={term}>
            <dt>{term}</dt>
            <dd>{text}</dd>
          </div>
        ))}
      </dl>
    </aside>
  );
}
