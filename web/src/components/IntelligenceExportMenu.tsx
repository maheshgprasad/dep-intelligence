import { useState } from "react";
import { InlineLoading, InlineNotification, MenuButton, MenuItem } from "@carbon/react";
import { downloadIntelligenceExport, type ExportFormat } from "../api";

const ITEMS: { format: ExportFormat; label: string }[] = [
  { format: "pdf", label: "PDF report" },
  { format: "markdown", label: "Markdown report" },
  { format: "json", label: "JSON report" },
  { format: "evidence", label: "Evidence package" },
];

export function IntelligenceExportMenu({ reportId }: { reportId: string }) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  async function download(format: ExportFormat, label: string) {
    setBusy(label);
    setError("");
    try {
      await downloadIntelligenceExport(reportId, format);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "The export could not be created.");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="stack-gap">
      <div className="button-row">
        {busy ? <InlineLoading description={`Preparing ${busy}`} status="active" /> : null}
        <MenuButton label="Export" kind="tertiary" size="sm" menuAlignment="bottom-end" disabled={Boolean(busy)}>
          {ITEMS.map((item) => (
            <MenuItem key={item.format} label={item.label} disabled={Boolean(busy)} onClick={() => void download(item.format, item.label)} />
          ))}
        </MenuButton>
      </div>
      {error ? <InlineNotification kind="error" title="Export" subtitle={error} lowContrast hideCloseButton /> : null}
    </div>
  );
}
