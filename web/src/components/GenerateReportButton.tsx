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

  const label = scope === "cluster" ? "Generate cluster report" : "Generate repository report";
  return (
    <div className="stack-gap">
      {staleCount > 0 ? (
        <p className="helper">This report will use currently published intelligence. {staleCount} service{staleCount === 1 ? " is" : "s are"} out of date.</p>
      ) : null}
      <div className="button-row">
        <Button kind="primary" size="sm" disabled={Boolean(busy) || (scope === "repository" && !service)} onClick={() => void generate(false)}>{label}</Button>
        <Button kind="ghost" size="sm" disabled={Boolean(busy)} onClick={() => void generate(true)}>Refresh intelligence and generate</Button>
      </div>
      {busy ? <InlineLoading description={busy} status="active" /> : null}
      {error ? <InlineNotification kind="error" title="Report" subtitle={error} lowContrast hideCloseButton /> : null}
    </div>
  );
}
