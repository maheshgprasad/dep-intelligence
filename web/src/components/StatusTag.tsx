import { Tag } from "@carbon/react";

export type StatusKind =
  | "current"
  | "stale"
  | "partial"
  | "unavailable"
  | "refreshing"
  | "critical"
  | "high"
  | "medium"
  | "low"
  | "info"
  | "verified"
  | "strong"
  | "moderate"
  | "weak"
  | "pass"
  | "warn"
  | "block"
  | "incomplete"
  | "passed"
  | "failed"
  | "skipped"
  | "timeout"
  | "cancelled"
  | "structure"
  | "history"
  | "both"
  | "cross"
  | "policy"
  | "verification"
  | "model";

const TYPE: Record<StatusKind, "green" | "red" | "magenta" | "purple" | "blue" | "cyan" | "teal" | "gray" | "cool-gray" | "warm-gray"> = {
  current: "green",
  stale: "magenta",
  partial: "purple",
  unavailable: "gray",
  refreshing: "cyan",
  critical: "red",
  high: "magenta",
  medium: "purple",
  low: "cyan",
  info: "cool-gray",
  verified: "green",
  strong: "teal",
  moderate: "purple",
  weak: "gray",
  pass: "green",
  warn: "purple",
  block: "red",
  incomplete: "magenta",
  passed: "green",
  failed: "red",
  skipped: "gray",
  timeout: "magenta",
  cancelled: "cool-gray",
  structure: "blue",
  history: "teal",
  both: "cyan",
  cross: "purple",
  policy: "blue",
  verification: "teal",
  model: "purple",
};

const LABEL: Record<StatusKind, string> = {
  current: "Current",
  stale: "Stale",
  partial: "Partial",
  unavailable: "Unavailable",
  refreshing: "Refreshing",
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
  info: "Info",
  verified: "Verified",
  strong: "Strong",
  moderate: "Moderate",
  weak: "Weak",
  pass: "Pass",
  warn: "Warn",
  block: "Block",
  incomplete: "Incomplete",
  passed: "Passed",
  failed: "Failed",
  skipped: "Skipped",
  timeout: "Timed out",
  cancelled: "Cancelled",
  structure: "Structure",
  history: "History",
  both: "Structure + history",
  cross: "Cross-repository",
  policy: "Policy",
  verification: "Verification",
  model: "Model-assisted",
};

export function statusKind(value: string): StatusKind | null {
  const key = value.trim().toLowerCase().replace(/[\s-]+/g, "_");
  const aliases: Record<string, StatusKind> = {
    structural: "structure",
    structural_only: "structure",
    temporal: "history",
    temporal_only: "history",
    hybrid: "both",
    both: "both",
    timed_out: "timeout",
    canceled: "cancelled",
    ai_assisted: "model",
    llm: "model",
  };
  if (key in TYPE) return key as StatusKind;
  return aliases[key] ?? null;
}

export function StatusTag({ status, label }: { status: string; label?: string }) {
  const kind = statusKind(status);
  const text = label ?? (kind ? LABEL[kind] : status);
  return (
    <Tag type={kind ? TYPE[kind] : "gray"} size="sm">
      {text}
    </Tag>
  );
}
