import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  ComboBox,
  Dropdown,
  InlineNotification,
  NumberInput,
  Search,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tag,
  TextInput,
  Tile,
} from "@carbon/react";
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

const ALL_SERVICES = { id: "*", label: "All services" };
const ALL_KINDS = { id: "*", label: "All kinds" };
const ALL_CATEGORIES = { id: "*", label: "All categories" };
const ALL_ORIGINS = { id: "*", label: "All origins" };
const ALL_FILES = "*";

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
  const [service, setService] = useState("*");
  const [kind, setKind] = useState("*");
  const [category, setCategory] = useState("code_symbol");
  const [file, setFile] = useState(ALL_FILES);
  const [origin, setOrigin] = useState("*");
  const [nameQuery, setNameQuery] = useState("");
  const [minHybrid, setMinHybrid] = useState(0);
  const [minStructural, setMinStructural] = useState(0);
  const [minTemporal, setMinTemporal] = useState(0);
  const [mode, setMode] = useState("code_change");
  const [ranking, setRanking] = useState("hybrid");
  const [asOf, setAsOf] = useState("");
  const [contractId, setContractId] = useState("");
  const [selectedKey, setSelectedKey] = useState("");
  const [result, setResult] = useState<HybridImpactResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [openKey, setOpenKey] = useState("");
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

  async function analyze(root?: { symbol?: string; file?: string }) {
    setBusy(true);
    setError("");
    try {
      setResult(
        await runHybridImpact({
          service: service === "*" ? "" : service,
          symbol: root?.symbol || "",
          file: root?.file || "",
          contract_id: contractId,
          mode,
          scenario: mode === "operational_failure" ? "http_unavailable" : "",
          graph_version: summary?.version || "",
          experiment: ranking,
          as_of: asOf || null,
          expand_temporal: ranking !== "structural_only",
          budget: { timeout_ms: 15000, max_nodes: 10000 },
        }),
      );
      setOpenKey("");
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
    () => [ALL_SERVICES, ...services.map((item) => ({ id: item.id, label: `${item.id} (${item.symbols})` }))],
    [services],
  );
  const kindItems = useMemo(() => {
    const values = [...new Set(catalog.map((node) => node.kind).filter(Boolean))].sort();
    return [ALL_KINDS, ...values.map((item) => ({ id: item, label: item }))];
  }, [catalog]);
  const categoryItems = useMemo(() => {
    const values = [...new Set(catalog.map((node) => node.category).filter(Boolean))].sort();
    return [ALL_CATEGORIES, { id: "code_symbol", label: "Code symbols" }, ...values.filter((item) => item !== "code_symbol").map((item) => ({ id: item, label: item }))];
  }, [catalog]);
  const originItems = useMemo(() => [ALL_ORIGINS, ...Object.entries(ORIGIN).map(([id, item]) => ({ id, label: item.label }))], []);
  const fileItems = useMemo(() => {
    const values = [
      ...new Set(
        catalog
          .filter((node) => service === "*" || node.service_id === service)
          .map((node) => node.file_path)
          .filter(Boolean),
      ),
    ].sort();
    return [ALL_FILES, ...values];
  }, [catalog, service]);
  const contractItems = useMemo(
    () => ["", ...contracts.filter((item) => service === "*" || item.service_id === service).map((item) => item.qualified_name)],
    [contracts, service],
  );

  const rows = useMemo(() => {
    if (result) return result.candidates.map(fromCandidate);
    return catalog.map(fromCatalog);
  }, [catalog, result]);

  const filtered = useMemo(() => {
    const needle = nameQuery.trim().toLowerCase();
    return rows.filter((row) => {
      if (service !== "*" && row.service_id !== service) return false;
      if (kind !== "*" && row.kind !== kind) return false;
      if (category !== "*" && row.category !== category) return false;
      if (file !== ALL_FILES && row.file_path !== file) return false;
      if (origin !== "*" && row.evidence_origin !== origin) return false;
      if (minHybrid > 0 && (row.hybrid_score ?? -1) < minHybrid) return false;
      if (minStructural > 0 && (row.structural_score ?? -1) < minStructural) return false;
      if (minTemporal > 0 && (row.temporal_score ?? -1) < minTemporal) return false;
      if (!needle) return true;
      return `${row.name} ${row.qualified_name} ${row.file_path}`.toLowerCase().includes(needle);
    });
  }, [rows, service, kind, category, file, origin, nameQuery, minHybrid, minStructural, minTemporal]);

  function rankVisible() {
    const selected = filtered.find((row) => row.key === selectedKey);
    if (selected) {
      void analyze({ symbol: selected.key });
      return;
    }
    void analyze({ file: file === ALL_FILES ? "" : file });
  }

  return (
    <div className="stack-gap">
      <div className="page-head">
        <div>
          <h1>Nami Rank Module</h1>
          <p>
            NRM loads the published snapshot, then ranks downstream change-impact candidates. Use the filters to narrow
            the table. Click a row to rank from that symbol; Rank filtered uses the current service and file filters.
            Scores are ranking heuristics, not probabilities of breakage.
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
      <div className="impact-layout">
        <div className="stack-gap nrm-filters">
          <p className="section-title">Filters</p>
          <Dropdown
            id="impact-service"
            titleText="Service"
            label="All services"
            items={serviceItems}
            itemToString={(item) => item?.label || ""}
            selectedItem={serviceItems.find((item) => item.id === service) || ALL_SERVICES}
            onChange={({ selectedItem }) => setService(selectedItem?.id || "*")}
          />
          <Dropdown
            id="impact-kind"
            titleText="Kind"
            label="All kinds"
            items={kindItems}
            itemToString={(item) => item?.label || ""}
            selectedItem={kindItems.find((item) => item.id === kind) || ALL_KINDS}
            onChange={({ selectedItem }) => setKind(selectedItem?.id || "*")}
          />
          <Dropdown
            id="impact-category"
            titleText="Category"
            label="Category"
            items={categoryItems}
            itemToString={(item) => item?.label || ""}
            selectedItem={categoryItems.find((item) => item.id === category) || ALL_CATEGORIES}
            onChange={({ selectedItem }) => setCategory(selectedItem?.id || "*")}
          />
          <ComboBox
            id="impact-file"
            titleText="File"
            placeholder="All files"
            items={fileItems}
            itemToString={(item) => (item === ALL_FILES ? "All files" : item || "")}
            selectedItem={file}
            onChange={({ selectedItem }) => setFile(selectedItem || ALL_FILES)}
          />
          <Dropdown
            id="impact-origin"
            titleText="Evidence origin"
            label="All origins"
            helperText={result ? "Applies to ranked candidates." : "Available after Rank filtered."}
            items={originItems}
            itemToString={(item) => item?.label || ""}
            selectedItem={originItems.find((item) => item.id === origin) || ALL_ORIGINS}
            onChange={({ selectedItem }) => setOrigin(selectedItem?.id || "*")}
          />
          <NumberInput
            id="impact-min-hybrid"
            label="Min hybrid"
            min={0}
            max={1}
            step={0.05}
            value={minHybrid}
            disabled={!result}
            onChange={(_event, { value }) => setMinHybrid(Number(value) || 0)}
          />
          <NumberInput
            id="impact-min-structural"
            label="Min structural"
            min={0}
            max={1}
            step={0.05}
            value={minStructural}
            disabled={!result}
            onChange={(_event, { value }) => setMinStructural(Number(value) || 0)}
          />
          <NumberInput
            id="impact-min-temporal"
            label="Min temporal"
            min={0}
            max={1}
            step={0.05}
            value={minTemporal}
            disabled={!result}
            onChange={(_event, { value }) => setMinTemporal(Number(value) || 0)}
          />
          <Search
            id="impact-name"
            labelText="Name contains"
            placeholder="Optional name or path contains"
            value={nameQuery}
            onChange={(event) => setNameQuery(event.target.value)}
            onClear={() => setNameQuery("")}
          />
          <p className="section-title">Ranking</p>
          <Dropdown
            id="impact-mode"
            titleText="Analysis mode"
            label="Mode"
            items={MODES}
            itemToString={(item) => item?.label || ""}
            selectedItem={MODES.find((item) => item.id === mode)}
            onChange={({ selectedItem }) => setMode(selectedItem?.id || "code_change")}
          />
          <Dropdown
            id="impact-ranking"
            titleText="Ranking"
            label="Ranking"
            items={RANKINGS}
            itemToString={(item) => item?.label || ""}
            selectedItem={RANKINGS.find((item) => item.id === ranking)}
            onChange={({ selectedItem }) => setRanking(selectedItem?.id || "hybrid")}
          />
          <TextInput
            id="impact-as-of"
            labelText="As of (optional ISO cutoff)"
            helperText="Later commits are excluded from temporal scores."
            value={asOf}
            onChange={(event) => setAsOf(event.target.value)}
          />
          <Dropdown
            id="impact-contract"
            titleText="Contract"
            label="Optional contract"
            items={contractItems}
            selectedItem={contractId}
            onChange={({ selectedItem }) => setContractId(selectedItem || "")}
          />
          <div className="button-row">
            <Button disabled={busy || !summary?.version} onClick={() => rankVisible()}>
              {busy ? "Running" : selectedKey ? "Rank selected" : "Rank filtered"}
            </Button>
            {result ? (
              <Button
                kind="ghost"
                onClick={() => {
                  setResult(null);
                  setOpenKey("");
                }}
              >
                Show snapshot
              </Button>
            ) : null}
          </div>
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
          {result && result.candidates.length === 0 ? (
            <InlineNotification kind="info" title="No candidates" subtitle="The root is in the snapshot and nothing passed the threshold." lowContrast hideCloseButton />
          ) : null}
          {result ? (
            <Tile>
              <p className="stat-label">
                {result.module_abbrev || "NRM"} · {rankingLabel(result.experiment)} · weights {result.weights.structural.toFixed(2)} /{" "}
                {result.weights.temporal.toFixed(2)} · provider {result.temporal_provider}
                {result.temporal_cutoff ? ` · cutoff ${result.temporal_cutoff}` : " · no temporal cutoff"}
              </p>
              <p>{result.score_meaning}</p>
            </Tile>
          ) : null}
          <p className="nrm-table-meta">
            Showing {filtered.length} of {rows.length} {result ? "ranked candidates" : "snapshot symbols"}
            {selectedKey ? " · click Rank selected to score from the highlighted symbol" : ""}
          </p>
          <Table>
            <TableHead>
              <TableRow>
                <TableHeader>Service</TableHeader>
                <TableHeader>Symbol</TableHeader>
                <TableHeader>Kind</TableHeader>
                <TableHeader>Location</TableHeader>
                <TableHeader>Origin</TableHeader>
                <TableHeader>Hybrid</TableHeader>
                <TableHeader>Structural</TableHeader>
                <TableHeader>Temporal</TableHeader>
              </TableRow>
            </TableHead>
            <TableBody>
              {filtered.map((row) => (
                <Fragment key={row.key}>
                  <TableRow
                    className={row.key === selectedKey ? "nrm-row-selected" : undefined}
                    onClick={() => {
                      setSelectedKey(row.key === selectedKey ? "" : row.key);
                      setOpenKey(row.candidate && openKey === row.key ? "" : row.candidate ? row.key : openKey);
                    }}
                  >
                    <TableCell>{row.service_id}</TableCell>
                    <TableCell>
                      {row.name}
                      <div className="stat-label">{row.category}</div>
                    </TableCell>
                    <TableCell>{row.kind || "—"}</TableCell>
                    <TableCell>
                      {row.file_path}
                      {row.line_start ? `:${row.line_start}` : ""}
                    </TableCell>
                    <TableCell>
                      {row.evidence_origin ? (
                        <Tag size="sm" type={ORIGIN[row.evidence_origin]?.type || "gray"}>
                          {ORIGIN[row.evidence_origin]?.label || row.evidence_origin}
                        </Tag>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell>{formatScore(row.hybrid_score)}</TableCell>
                    <TableCell>{formatScore(row.structural_score)}</TableCell>
                    <TableCell>{formatScore(row.temporal_score)}</TableCell>
                  </TableRow>
                  {row.candidate && openKey === row.key ? (
                    <TableRow key={`${row.key}-path`}>
                      <TableCell colSpan={8}>
                        <EvidencePanel row={row.candidate} />
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

function fromCatalog(node: ClusterNode): DisplayRow {
  return {
    key: node.key,
    service_id: node.service_id,
    name: node.name,
    qualified_name: node.qualified_name,
    file_path: node.file_path,
    line_start: node.line_start,
    category: node.category,
    kind: node.kind,
    evidence_origin: "",
    hybrid_score: null,
    structural_score: null,
    temporal_score: null,
    hops: null,
    candidate: null,
  };
}

function fromCandidate(row: HybridCandidate): DisplayRow {
  return {
    key: row.key,
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

function formatScore(value: number | null): string {
  return value == null ? "—" : value.toFixed(3);
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
