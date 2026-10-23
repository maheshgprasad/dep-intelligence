import { useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  ComboBox,
  Dropdown,
  InlineNotification,
  Tag,
  TextInput,
  Tile,
} from "@carbon/react";
import { CarbonTable } from "../components/CarbonTable";
import {
  clusterContracts,
  clusterGraph,
  refreshCluster,
  runHybridImpact,
  type ClusterNode,
  type ClusterSummary,
  type HybridCandidate,
  type HybridImpactResponse,
} from "../api";

const MODES = [
  { id: "code_change", label: "Code change" },
  { id: "contract_change", label: "Contract change" },
  { id: "operational_failure", label: "Operational failure" },
];

const RANKINGS = [
  { id: "hybrid", label: "NRM (structural + temporal)" },
  { id: "structural_only", label: "Structural only" },
  { id: "temporal_only", label: "Temporal only" },
];

const ORIGIN: Record<string, { label: string; type: "teal" | "blue" | "purple" | "gray" }> = {
  both: { label: "Both", type: "teal" },
  structural_only: { label: "Structural", type: "blue" },
  temporal_only: { label: "Temporal", type: "purple" },
};

const ALL_ORIGINS = { id: "*", label: "All evidence" };

const RESULT_HEADERS = [
  { key: "rank", header: "Rank" },
  { key: "service", header: "Repository / service" },
  { key: "file", header: "File" },
  { key: "symbol", header: "Strongest symbol" },
  { key: "origin", header: "Evidence" },
  { key: "hybrid", header: "NRM" },
  { key: "structural", header: "Structural" },
  { key: "temporal", header: "Temporal" },
];

type DisplayRow = {
  key: string;
  service_id: string;
  name: string;
  qualified_name: string;
  file_path: string;
  line_start: number | null;
  category: string;
  kind: string;
  evidence_origin: string;
  hybrid_score: number | null;
  structural_score: number | null;
  temporal_score: number | null;
  hops: number | null;
  candidate: HybridCandidate | null;
};

export function ImpactPage({ tick, resync }: { tick: number; resync: string }) {
  const [summary, setSummary] = useState<ClusterSummary | null>(null);
  const [catalog, setCatalog] = useState<ClusterNode[]>([]);
  const [contracts, setContracts] = useState<{ key: string; name: string; service_id: string; qualified_name: string }[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [service, setService] = useState("");
  const [file, setFile] = useState("");
  const [origin, setOrigin] = useState("*");
  const [nameQuery, setNameQuery] = useState("");
  const [mode, setMode] = useState("code_change");
  const [ranking, setRanking] = useState("hybrid");
  const [asOf, setAsOf] = useState("");
  const [contractId, setContractId] = useState("");
  const [selectedKey, setSelectedKey] = useState("");
  const [result, setResult] = useState<HybridImpactResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const sawSnapshot = useRef(false);

  async function load() {
    if (!sawSnapshot.current) setLoading(true);
    try {
      const graph = await clusterGraph({ limit: 5000 });
      const listed = await clusterContracts();
      setSummary((current) => (sameSummary(current, graph.summary) ? current : graph.summary));
      setCatalog((current) => (sameNodes(current, graph.page.nodes) ? current : graph.page.nodes));
      setContracts((current) => (sameContracts(current, listed.contracts) ? current : listed.contracts));
      if (!sawSnapshot.current) setError("");
      sawSnapshot.current = true;
    } catch {
      if (!sawSnapshot.current) setError("The cluster snapshot is not available yet. Refresh after the code graphs exist.");
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
        await runHybridImpact({
          service,
          symbol: "",
          file,
          contract_id: mode === "contract_change" ? contractId : "",
          mode,
          scenario: mode === "operational_failure" ? "http_unavailable" : "",
          graph_version: summary?.version || "",
          experiment: ranking,
          as_of: asOf || null,
          expand_temporal: ranking !== "structural_only",
          budget: { timeout_ms: 15000, max_nodes: 10000 },
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
  const serviceItems = useMemo(
    () => services.map((item) => ({ id: item.id, label: `${item.id} (${item.symbols})` })),
    [services],
  );
  const originItems = useMemo(() => [ALL_ORIGINS, ...Object.entries(ORIGIN).map(([id, item]) => ({ id, label: item.label }))], []);
  const fileItems = useMemo(() => {
    return [
      ...new Set(
        catalog
          .filter((node) => node.service_id === service)
          .map((node) => node.file_path)
          .filter(Boolean),
      ),
    ].sort();
  }, [catalog, service]);
  const contractItems = useMemo(
    () => contracts.filter((item) => item.service_id === service).map((item) => item.qualified_name),
    [contracts, service],
  );

  useEffect(() => {
    if (!service && services[0]) setService(services[0].id);
  }, [service, services]);

  const rows = useMemo(() => (result ? result.candidates.map(fromCandidate) : []), [result]);

  const filtered = useMemo(() => {
    const needle = nameQuery.trim().toLowerCase();
    return rows.filter((row) => {
      if (origin !== "*" && row.evidence_origin !== origin) return false;
      if (!needle) return true;
      return `${row.name} ${row.qualified_name} ${row.file_path} ${row.service_id}`.toLowerCase().includes(needle);
    });
  }, [rows, origin, nameQuery]);

  useEffect(() => {
    if (!filtered.length) {
      if (selectedKey) setSelectedKey("");
      return;
    }
    if (!filtered.some((row) => row.key === selectedKey)) setSelectedKey(filtered[0].key);
  }, [filtered, selectedKey]);

  function chooseService(next: string) {
    setService(next);
    setFile("");
    setContractId("");
    setResult(null);
    setSelectedKey("");
  }

  const ready = Boolean(summary?.version) && (mode === "contract_change" ? Boolean(contractId) : Boolean(file));
  const selected = filtered.find((row) => row.key === selectedKey) ?? null;
  const tableRows = filtered.map((row) => ({
    id: row.key,
    rank: row.candidate?.rank ?? 0,
    service: row.service_id,
    file: row.file_path || "—",
    symbol: row.name,
    origin: row.evidence_origin,
    hybrid: row.hybrid_score ?? -1,
    structural: row.structural_score ?? -1,
    temporal: row.temporal_score ?? -1,
  }));

  return (
    <div className="stack-gap">
      <div className="page-head">
        <div>
          <h1>Nami Rank Module</h1>
          <div className="button-row">
            <Tag type="blue">NRM v2</Tag>
            <Tag type="teal">File-level ranking</Tag>
            <Tag type="gray">Scores are not probabilities</Tag>
          </div>
          <p>
            Rank downstream files using structural dependency evidence and historical change evidence. Scores are ranking
            heuristics, not probabilities of breakage.
          </p>
        </div>
        <Button kind="secondary" onClick={() => void refreshCluster().then(() => load())}>
          Refresh graph
        </Button>
      </div>
      {resync ? <InlineNotification kind="warning" title="Resync" subtitle={resync} lowContrast hideCloseButton /> : null}
      {error ? <InlineNotification kind="error" title="Nami Rank" subtitle={error} lowContrast hideCloseButton /> : null}
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
            {summary.node_count} symbols and contracts, {summary.edge_count} edges, {summary.unresolved_count} unresolved
            references.
            {stale.length ? ` Stale services: ${stale.join(", ")}.` : " Index quality is current for every loaded partition."}
          </p>
        </Tile>
      ) : null}
      <section className="stack-gap">
        <p className="section-title">1. What changed?</p>
        <div className="nrm-toolbar">
          <Dropdown
            id="impact-service"
            titleText="Repository / service"
            label="Select a service"
            items={serviceItems}
            itemToString={(item) => item?.label || ""}
            selectedItem={serviceItems.find((item) => item.id === service) || null}
            onChange={({ selectedItem }) => chooseService(selectedItem?.id || "")}
          />
          <ComboBox
            id="impact-file"
            titleText="File"
            placeholder="Select a file"
            items={fileItems}
            itemToString={(item) => item || ""}
            selectedItem={file || null}
            onChange={({ selectedItem }) => {
              setFile(selectedItem || "");
              setResult(null);
              setSelectedKey("");
            }}
          />
          <Dropdown
            id="impact-mode"
            titleText="Change type"
            label="Change type"
            items={MODES}
            itemToString={(item) => item?.label || ""}
            selectedItem={MODES.find((item) => item.id === mode)}
            onChange={({ selectedItem }) => {
              setMode(selectedItem?.id || "code_change");
              setResult(null);
            }}
          />
          <Dropdown
            id="impact-ranking"
            titleText="Ranking"
            label="Ranking"
            items={RANKINGS}
            itemToString={(item) => item?.label || ""}
            selectedItem={RANKINGS.find((item) => item.id === ranking)}
            onChange={({ selectedItem }) => {
              setRanking(selectedItem?.id || "hybrid");
              setResult(null);
            }}
          />
        </div>
        <div className="nrm-toolbar">
          {mode === "contract_change" ? (
            <Dropdown
              id="impact-contract"
              titleText="API / contract"
              label="Select a contract"
              items={contractItems}
              selectedItem={contractId || null}
              onChange={({ selectedItem }) => {
                setContractId(selectedItem || "");
                setResult(null);
              }}
            />
          ) : null}
          <TextInput
            id="impact-as-of"
            labelText="Analyze as of"
            helperText="Optional ISO date. Later commits are left out of the temporal score."
            value={asOf}
            onChange={(event) => {
              setAsOf(event.target.value);
              setResult(null);
            }}
          />
          <div className="button-row">
            <Button disabled={busy || !ready} onClick={() => void analyze()}>
              Run Nami Rank
            </Button>
          </div>
        </div>
        {!file && mode !== "contract_change" ? (
          <p className="stat-label">{service ? "Select a file, then run Nami Rank." : "Select a service, then a file."}</p>
        ) : null}
        {busy ? <InlineNotification kind="info" title="Ranking downstream impact…" subtitle="Structural and temporal evidence are being combined." lowContrast hideCloseButton /> : null}
      </section>
      <section className="stack-gap">
        {result?.truncated ? (
          <InlineNotification kind="warning" title="Truncated" subtitle={result.truncation_reason || "The search hit a budget."} lowContrast hideCloseButton />
        ) : null}
        {result && result.candidates.length === 0 ? (
          <InlineNotification kind="info" title="No downstream files" subtitle="Nothing downstream met the current analysis. Try another file or structural-only ranking." lowContrast hideCloseButton />
        ) : null}
        {result?.causal_validity && result.causal_validity.overall === false && result.temporal_cutoff ? (
          <InlineNotification
            kind="warning"
            title="Partial historical alignment"
            subtitle="Temporal history respects the cutoff, but structural topology is from the current snapshot."
            lowContrast
            hideCloseButton
          />
        ) : null}
        {!result ? (
          <Tile>
            <p className="section-title">Ranked files</p>
            <p>Choose a service and file, then run Nami Rank. The table and evidence update from that result.</p>
          </Tile>
        ) : (
          <>
            <Tile>
              <p className="stat-label">
                Ranked files {filtered.length} of {rows.length}
                {result.fusion?.method ? ` · ${result.fusion.method}` : ""}
                {result.root?.file_path ? ` · changed ${result.root.service_id}/${result.root.file_path}` : ""}
              </p>
              <p className="stat-label">
                {rankingLabel(result.experiment)} · provider {result.temporal_provider}
                {result.temporal_cutoff ? ` · cutoff ${result.temporal_cutoff}` : " · current-state analysis"}
              </p>
            </Tile>
            <CarbonTable
              title="Ranked files"
              description="Select a row to load its evidence."
              headers={RESULT_HEADERS}
              rows={tableRows}
              query={nameQuery}
              onQueryChange={setNameQuery}
              searchPlaceholder="File, symbol, or service"
              selectedId={selectedKey}
              onRowClick={setSelectedKey}
              empty="No ranked files match this evidence filter or search."
              toolbar={
                <div className="carbon-table-filter">
                  <Dropdown
                    id="impact-origin"
                    titleText="Evidence"
                    label="All evidence"
                    items={originItems}
                    itemToString={(item) => item?.label || ""}
                    selectedItem={originItems.find((item) => item.id === origin) || ALL_ORIGINS}
                    onChange={({ selectedItem }) => setOrigin(selectedItem?.id || "*")}
                  />
                </div>
              }
              renderCell={(key, value) => formatCell(key, value)}
            />
            {selected?.candidate ? (
              <Tile>
                <p className="section-title">
                  {selected.service_id} / {selected.file_path || selected.name}
                </p>
                <EvidencePanel row={selected.candidate} />
              </Tile>
            ) : (
              <InlineNotification kind="info" title="No matching files" subtitle="Change the evidence filter or clear the search to see ranked files again." lowContrast hideCloseButton />
            )}
            {result.limitations?.length ? <p className="tab-hint">{result.limitations[0]}</p> : null}
          </>
        )}
      </section>
    </div>
  );
}

function formatCell(header: string, value: unknown): string | JSX.Element {
  if (header === "origin") {
    const kind = String(value);
    if (!kind) return "—";
    return (
      <Tag size="sm" type={ORIGIN[kind]?.type || "gray"}>
        {ORIGIN[kind]?.label || kind}
      </Tag>
    );
  }
  if (header === "hybrid" || header === "structural" || header === "temporal") {
    const score = Number(value);
    return score < 0 ? "—" : score.toFixed(3);
  }
  return String(value ?? "—");
}

function candidateId(row: HybridCandidate): string {
  if (typeof row.key === "object" && row.key) return `${row.key.service_id}:${row.key.file_path}`;
  return row.sort_key || String(row.key);
}

function fromCandidate(row: HybridCandidate): DisplayRow {
  return {
    key: candidateId(row),
    service_id: row.service_id,
    name: row.name,
    qualified_name: row.qualified_name,
    file_path: row.file_path,
    line_start: row.line_start,
    category: row.category,
    kind: row.kind,
    evidence_origin: row.evidence_origin,
    hybrid_score: row.hybrid_score,
    structural_score: row.structural.available ? row.structural.score : null,
    temporal_score: row.temporal.available ? row.temporal.score : null,
    hops: row.structural.hops,
    candidate: row,
  };
}

function EvidencePanel({ row }: { row: HybridCandidate }) {
  const path = row.structural.path ?? [];
  const segments = row.temporal.temporal_path_evidence ?? [];
  return (
    <div className="stack-gap">
      {row.why_ranked.length ? (
        <div>
          <p className="stat-label">Why ranked</p>
          <ol className="path-list">
            {row.why_ranked.map((fact) => (
              <li key={fact}>{fact}</li>
            ))}
          </ol>
        </div>
      ) : null}
      <div>
        <p className="stat-label">Structural path</p>
        <ol className="path-list">
          {path.length === 0 ? <li>{row.structural.available ? "Root of the analysis." : "Not structurally reachable in this snapshot."}</li> : null}
          {path.map((step, index) => (
            <li key={`${row.key}-struct-${index}`}>
              {step.relation} {step.direction} × {step.weight} — {step.reason}
              {step.evidence?.status ? ` (${step.evidence.status}${step.evidence.source ? `, ${step.evidence.source}` : ""})` : ""}
            </li>
          ))}
        </ol>
      </div>
      <div>
        <p className="stat-label">
          Temporal path evidence
          {row.temporal.total_segments ? ` · ${row.temporal.available_segments}/${row.temporal.total_segments} segments` : ""}
        </p>
        <ol className="path-list">
          {segments.length === 0 ? (
            <li>
              {row.temporal.available
                ? row.temporal.observation || "Historical neighbour; no structural file path."
                : "No temporal evidence for this candidate. Temporal contribution is zero."}
            </li>
          ) : null}
          {segments.map((segment, index) => (
            <li key={`${row.key}-temp-${index}`}>
              {segment.source} → {segment.target}: {segment.available ? segment.score.toFixed(3) : "unavailable"} · {segment.evidence_kind || "none"}
              {segment.available ? ` · ${segment.support} of ${segment.source_opportunities} source events` : ""}
              {segment.observation ? ` — ${segment.observation}` : ""}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

function rankingLabel(experiment: string): string {
  return RANKINGS.find((item) => item.id === experiment)?.label || experiment;
}

function sameSummary(current: ClusterSummary | null, next: ClusterSummary): boolean {
  if (!current) return false;
  return (
    current.version === next.version &&
    current.node_count === next.node_count &&
    current.edge_count === next.edge_count &&
    current.unresolved_count === next.unresolved_count &&
    (current.stale_services ?? []).join() === (next.stale_services ?? []).join() &&
    (current.notices ?? []).join() === (next.notices ?? []).join() &&
    (current.config_errors ?? []).join() === (next.config_errors ?? []).join()
  );
}

function sameContracts(
  current: { key: string; qualified_name: string }[],
  next: { key: string; qualified_name: string }[],
): boolean {
  if (current.length !== next.length) return false;
  return current.every((item, index) => item.key === next[index]?.key && item.qualified_name === next[index]?.qualified_name);
}

function sameNodes(current: ClusterNode[], next: ClusterNode[]): boolean {
  if (current.length !== next.length) return false;
  return current.every((item, index) => item.key === next[index]?.key);
}
