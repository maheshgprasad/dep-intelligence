import { Information } from "@carbon/icons-react";
import { Toggletip, ToggletipButton, ToggletipContent } from "@carbon/react";

export function ScoreHelp({ label = "Impact rank" }: { label?: string }) {
  return (
    <span className="inline-help">
      {label}
      <Toggletip align="bottom-right">
        <ToggletipButton label="Why scores are not probabilities">
          <Information size={16} />
        </ToggletipButton>
        <ToggletipContent>
          <p>
            Used to order downstream review targets. It is not a probability that the change will cause a failure.
          </p>
        </ToggletipContent>
      </Toggletip>
    </span>
  );
}
