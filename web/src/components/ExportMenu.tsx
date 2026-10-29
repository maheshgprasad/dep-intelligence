import { useState } from "react";
import { InlineLoading, InlineNotification, MenuButton, MenuItem } from "@carbon/react";
import { downloadReviewExport, type ExportFormat } from "../api";

const ITEMS: { format: ExportFormat; label: string }[] = [
  { format: "pdf", label: "PDF report" },
  { format: "markdown", label: "Markdown report" },
  { format: "json", label: "JSON report" },
  { format: "evidence", label: "Evidence package" },
];

export function ExportMenu({ reviewId, summary }: { reviewId: string; summary: string }) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  async function download(format: ExportFormat, label: string) {
    setBusy(label);
    setError("");
    setCopied(false);
    try {
      await downloadReviewExport(reviewId, format);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "The export could not be created.");
    } finally {
      setBusy("");
    }
  }

  async function copy() {
    setError("");
    try {
      await navigator.clipboard.writeText(summary);
      setCopied(true);
    } catch {
      setError("The summary could not be copied.");
    }
  }

  return (
    <div className="stack-gap">
      <div className="button-row">
        {busy ? <InlineLoading description={busy} status="active" /> : null}
        <MenuButton label="Export" kind="tertiary" size="sm" menuAlignment="bottom-end" disabled={Boolean(busy)}>
          {ITEMS.map((item) => (
            <MenuItem key={item.format} label={item.label} disabled={Boolean(busy)} onClick={() => void download(item.format, item.label)} />
          ))}
          <MenuItem label="Copy summary" disabled={Boolean(busy)} onClick={() => void copy()} />
        </MenuButton>
      </div>
      {copied ? <InlineNotification kind="success" title="Copied" subtitle="The summary is on the clipboard." lowContrast hideCloseButton /> : null}
      {error ? <InlineNotification kind="error" title="Export" subtitle={error} lowContrast hideCloseButton /> : null}
    </div>
  );
}
