import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, InlineLoading, InlineNotification } from "@carbon/react";
import { createIntelligenceReport } from "../api";

export function GenerateReportButton({
  scope,
  service = "",
  staleCount = 0,
}: {
  scope: "cluster" | "repository";
  service?: string;
  staleCount?: number;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  async function generate(refresh: boolean) {
    setBusy(refresh ? "Refreshing published intelligence" : "Collecting current intelligence");
    setError("");
    try {
      const report = await createIntelligenceReport({ scope, service, refresh });
      navigate(`/reports/${report.report_id}`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "The report could not be generated.");
    } finally {
      setBusy("");
    }
  }

  const label = scope === "cluster" ? "Workspace report" : `Repository report${service ? ` · ${service}` : ""}`;
  return (
    <div className="stack-gap">
      <p className="helper">{label}. Reports use the published snapshot and do not rerun Boundary Gate or Nami Rank.</p>
      {staleCount > 0 ? (
        <InlineNotification kind="warning" title="Current intelligence is out of date" subtitle={`${staleCount} service${staleCount === 1 ? " is" : "s are"} out of date. Generate from the current snapshot, or refresh intelligence first.`} lowContrast hideCloseButton />
      ) : null}
      <div className="button-row">
        <Button kind="primary" size="sm" disabled={Boolean(busy) || (scope === "repository" && !service)} onClick={() => void generate(false)}>Generate from current intelligence</Button>
        <Button kind="ghost" size="sm" disabled={Boolean(busy)} onClick={() => void generate(true)}>Refresh intelligence first</Button>
      </div>
      {busy ? <InlineLoading description={busy} status="active" /> : null}
      {error ? <InlineNotification kind="error" title="Report" subtitle={error} lowContrast hideCloseButton /> : null}
    </div>
  );
}
