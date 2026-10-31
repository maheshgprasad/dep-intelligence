export const DECISION_COPY: Record<string, { heading: string; label: string; note: string }> = {
  pass: {
    heading: "Ready to proceed",
    label: "PASS",
    note: "Evidence complete. Required checks that ran passed. No verified blocker.",
  },
  warn: {
    heading: "Review recommended",
    label: "WARN",
    note: "No verified blocker. Review work remains.",
  },
  block: {
    heading: "Action required before proceeding",
    label: "BLOCK",
    note: "A verified blocker triggered a gate rule. A required action is listed.",
  },
  incomplete: {
    heading: "More evidence is needed",
    label: "INCOMPLETE",
    note: "Evidence is unavailable or inconclusive, so the decision cannot be completed.",
  },
};

export const ALIGNMENT_LABELS: Record<string, string> = {
  exact: "Exact match",
  compatible: "Current enough",
  stale: "Out of date",
  unavailable: "Unavailable",
};

export const SOURCE_LABELS: Record<string, string> = {
  diff: "Code diff",
  CRG: "Code graph",
  cluster_graph: "Cross-service dependency graph",
  manifest: "Service manifest",
  OpenAPI: "OpenAPI",
  git_history: "Repository history",
  NRM: "Nami Rank",
  test: "Tests",
  verification_command: "Verification",
  policy: "Project policy",
  LLM: "Model suggestion",
};

export const EVIDENCE_QUALITY: Record<string, string> = {
  verified: "Nami has deterministic evidence sufficient for this specific claim.",
  strong: "Multiple supporting evidence sources exist, but the claim was not directly verified by a deterministic check.",
  moderate: "Some evidence supports the claim, but important confirmation is missing.",
  weak: "The result should be treated primarily as a review target.",
  unsupported: "The claim does not have enough evidence to publish.",
};

export const CHECK_STATUS: Record<string, string> = {
  passed: "Passed",
  failed: "Failed",
  warning: "Needs review",
  not_evaluated: "Not evaluated",
  unavailable: "Unavailable",
};

export function decisionCopy(decision: string): { heading: string; label: string; note: string } {
  return DECISION_COPY[decision] ?? DECISION_COPY.incomplete;
}

export function alignmentLabel(value: string | undefined): string {
  if (!value) return "Not recorded";
  return ALIGNMENT_LABELS[value] ?? value;
}

export const VALIDATION_STATUS: Record<string, string> = {
  validated: "Validated",
  validated_with_limitations: "Validated with limitations",
  failed: "Validation failed",
};

export const VALIDATION_HELP: Record<string, string> = {
  validated: "Nami independently reproduced the Gate result from the stored evidence and rule requirements.",
  validated_with_limitations: "The Gate result was reproduced, but one or more optional validation checks could not be completed.",
  failed: "The independent validator could not justify or reproduce the stored Gate decision.",
};

export function validationLabel(status: string | undefined): string {
  if (!status) return "Validation not recorded";
  return VALIDATION_STATUS[status] ?? status;
}

export function validationHelp(status: string | undefined): string {
  if (!status) return "";
  return VALIDATION_HELP[status] ?? "";
}

export function sourceLabel(value: string | undefined): string {
  if (!value) return "Source not recorded";
  return SOURCE_LABELS[value] ?? value;
}
