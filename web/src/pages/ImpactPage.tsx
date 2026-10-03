import { Fragment, useEffect, useState } from "react";
import {
  Button,
  Dropdown,
  InlineNotification,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TextInput,
  Tile,
} from "@carbon/react";
import {
  clusterContracts,
  clusterGraph,
  refreshCluster,
  runImpact,
  type ClusterSummary,
  type ImpactResponse,
} from "../api";

const MODES = [
  { id: "code_change", label: "Code change" },
  { id: "contract_change", label: "Contract change" },
  { id: "operational_failure", label: "Operational failure" },
];

export function ImpactPage({ tick, resync }: { tick: number; resync: string }) {
  const [summary, setSummary] = useState<ClusterSummary | null>(null);
  const [contracts, setContracts] = useState<{ key: string; name: string; service_id: string; qualified_name: string }[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [service, setService] = useState("");
  const [mode, setMode] = useState("code_change");
  const [symbol, setSymbol] = useState("");
  const [file, setFile] = useState("");
  const [line, setLine] = useState("");
  const [contractId, setContractId] = useState("");
  const [result, setResult] = useState<ImpactResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [openKey, setOpenKey] = useState("");

  async function load() {
    setLoading(true);
    try {
      const graph = await clusterGraph();
      const listed = await clusterContracts();
      setSummary(graph.summary);
      setContracts(listed.contracts);
      setService((current) => current || graph.summary.services?.[0]?.id || "");
      setError("");
    } catch {
      setError("The cluster snapshot is not available yet. Refresh after the code graphs exist.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, [tick]);

  async function analyze() {
    setBusy(true);
    setError("");
    try {
      setResult(
        await runImpact({
          service,
          symbol,
          file,
          line: line ? Number(line) : null,
          contract_id: contractId,
          mode,
          scenario: mode === "operational_failure" ? "http_unavailable" : "",
          graph_version: summary?.version || "",
        }),
      );
    } catch (exc) {
      setResult(null);
      const status = exc instanceof Error && "status" in exc ? Number((exc as { status?: number }).status) : 0;
      setError(
        status === 409
          ? `${exc instanceof Error ? exc.message : "The graph changed."} Reload the snapshot and run the query again.`
          : exc instanceof Error
            ? exc.message
            : "Impact query failed",
      );
    } finally {
      setBusy(false);
    }
  }

  const services = summary?.services ?? [];
  const stale = summary?.stale_services ?? [];

  return (
    <div className="stack-gap">
      <div className="page-head">
        <div>
          <h1>Cross-repository impact</h1>
          <p>Structural dependents of a symbol or contract. The score is the strongest path weight, not a chance of failure.</p>
        </div>
        <Button kind="secondary" onClick={() => void refreshCluster().then(() => load())}>
          Refresh graph
        </Button>
      </div>
      {resync ? <InlineNotification kind="warning" title="Resync" subtitle={resync} lowContrast hideCloseButton /> : null}
      {error ? <InlineNotification kind="error" title="Impact" subtitle={error} lowContrast hideCloseButton /> : null}
      {loading ? <InlineNotification kind="info" title="Loading" subtitle="Reading the published cluster snapshot." lowContrast hideCloseButton /> : null}
      {!loading && !summary?.version ? (
        <InlineNotification
          kind="info"
          title="No cluster snapshot"
          subtitle="Build code-review-graph databases for the allowlisted repositories, then refresh the cluster graph."
          lowContrast
          hideCloseButton
        />
      ) : null}
      {summary?.notices?.map((notice) => (
        <InlineNotification key={notice} kind="info" title="Repositories" subtitle={notice} lowContrast hideCloseButton />
      ))}
      {summary?.config_errors?.length ? (
        <InlineNotification
          kind="warning"
          title="Contract map"
          subtitle={`repos.txt decides which repositories are analyzed${summary.allowlist?.length ? `: ${summary.allowlist.join(", ")}` : ""}. cluster-manifest.json only adds cross-service links, and this copy does not match that list. ${summary.config_errors.join(" ")}`}
          lowContrast
          hideCloseButton
        />
      ) : null}
      {summary?.version ? (
        <Tile>
          <p className="stat-label">Snapshot {summary.version.slice(0, 12)}</p>
          <p>
            {summary.node_count} symbols and contracts, {summary.edge_count} edges, {summary.unresolved_count} unresolved references.
            {stale.length ? ` Stale services: ${stale.join(", ")}.` : " Index quality is current for every loaded partition."}
          </p>
        </Tile>
      ) : null}
      <div className="impact-layout">
        <div className="stack-gap">
          <Dropdown
            id="impact-service"
            titleText="Service"
            label="Choose a service"
            items={services.map((item) => item.id)}
            selectedItem={service}
            onChange={({ selectedItem }) => setService(selectedItem || "")}
          />
          <Dropdown
            id="impact-mode"
            titleText="Analysis mode"
            label="Mode"
            items={MODES}
            itemToString={(item) => item?.label || ""}
            selectedItem={MODES.find((item) => item.id === mode)}
            onChange={({ selectedItem }) => setMode(selectedItem?.id || "code_change")}
          />
          <TextInput id="impact-symbol" labelText="Symbol" value={symbol} onChange={(event) => setSymbol(event.target.value)} />
          <TextInput id="impact-file" labelText="File" value={file} onChange={(event) => setFile(event.target.value)} />
          <TextInput id="impact-line" labelText="Line" value={line} onChange={(event) => setLine(event.target.value)} />
          <Dropdown
            id="impact-contract"
            titleText="Contract"
            label="Optional contract"
            items={["", ...contracts.filter((item) => !service || item.service_id === service).map((item) => item.qualified_name)]}
            selectedItem={contractId}
            onChange={({ selectedItem }) => setContractId(selectedItem || "")}
          />
          <Button disabled={busy || !summary?.version} onClick={() => void analyze()}>
            {busy ? "Running" : "Run impact"}
          </Button>
        </div>
        <div className="table-scroll">
          {result?.truncated ? (
            <InlineNotification
              kind="warning"
              title="Truncated"
              subtitle={result.truncation_reason || "The search hit a budget."}
              lowContrast
              hideCloseButton
            />
          ) : null}
          {result && result.affected.length === 0 ? (
            <InlineNotification kind="info" title="No affected symbols" subtitle="The root is in the snapshot and nothing passed the threshold." lowContrast hideCloseButton />
          ) : null}
          <Table>
            <TableHead>
              <TableRow>
                <TableHeader>Service</TableHeader>
                <TableHeader>Symbol</TableHeader>
                <TableHeader>Location</TableHeader>
                <TableHeader>Structural score</TableHeader>
                <TableHeader>Confidence</TableHeader>
              </TableRow>
            </TableHead>
            <TableBody>
              {(result?.affected ?? []).map((row) => (
                <Fragment key={row.key}>
                  <TableRow onClick={() => setOpenKey(openKey === row.key ? "" : row.key)}>
                    <TableCell>{row.service_id}</TableCell>
                    <TableCell>
                      {row.name}
                      <div className="stat-label">{row.category}</div>
                    </TableCell>
                    <TableCell>
                      {row.file_path}
                      {row.line_start ? `:${row.line_start}` : ""}
                    </TableCell>
                    <TableCell>{row.structural_score.toFixed(3)}</TableCell>
                    <TableCell>{row.confidence == null ? "unknown" : row.confidence.toFixed(2)}</TableCell>
                  </TableRow>
                  {openKey === row.key ? (
                    <TableRow key={`${row.key}-path`}>
                      <TableCell colSpan={5}>
                        <ol className="path-list">
                          {row.path.length === 0 ? <li>Root of the analysis.</li> : null}
                          {row.path.map((step, index) => (
                            <li key={`${row.key}-${index}`}>
                              {step.relation} {step.direction} × {step.weight} — {step.reason}
                              {step.evidence?.status ? ` (${step.evidence.status}${step.evidence.source ? `, ${step.evidence.source}` : ""})` : ""}
                            </li>
                          ))}
                        </ol>
                      </TableCell>
                    </TableRow>
                  ) : null}
                </Fragment>
              ))}
            </TableBody>
          </Table>
          {result?.limitations?.length ? <p className="tab-hint">{result.limitations[0]}</p> : null}
        </div>
      </div>
    </div>
  );
}
