import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Information } from "@carbon/icons-react";
import {
  Button,
  Column,
  ComboBox,
  DefinitionTooltip,
  Grid,
  InlineLoading,
  InlineNotification,
  Search,
  SkeletonText,
  Tab,
  TabList,
  TabPanel,
  TabPanels,
  Tabs,
} from "@carbon/react";
import { Link } from "react-router-dom";
import { queryGraph, repositoryArchitecture, type AppState, type Cochange, type CrgBundle, type CrgPayload } from "../api";
import { NamiDataTable } from "../components/NamiDataTable";
import { NamiEmptyState } from "../components/NamiEmptyState";
import { PageHeader } from "../components/PageHeader";
import { StatusTag } from "../components/StatusTag";
import { graphQueryTarget, relativePath, repoSymbol, symbolName, visibleFields } from "../labels";

const ACTIONS = [
  ["Callers", "callers_of"],
  ["Calls", "callees_of"],
  ["Imports", "imports_of"],
  ["References", "references_to"],
  ["Tests", "tests_for"],
  ["Flows", "flows"],
] as const;

export function ArchitecturePage({ snapshot, loaded, error }: { snapshot: AppState | null; loaded: boolean; error: string }) {
  const [params, setParams] = useSearchParams();
  const repos = snapshot?.repositories ?? [];
  const service = params.get("service") || "";
  const selected = repos.find((repo) => repo.slug === service || repo.name === service) ?? repos[0];
  const [bundle, setBundle] = useState<{ crg: CrgBundle | null; cochange: Cochange | null } | null>(null);
  const [loadingRepo, setLoadingRepo] = useState(false);
  const tools = bundle?.crg?.tools;
  const cochange = bundle?.cochange ?? undefined;
  const [query, setQuery] = useState(params.get("q") || "");
  const [target, setTarget] = useState(params.get("target") || "");
  const [targetFile, setTargetFile] = useState("");
  const [live, setLive] = useState<CrgPayload | null>(null);
  const [liveError, setLiveError] = useState("");
  const [busy, setBusy] = useState(false);
  const searched = useRef("");

  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    let current = true;
    setLoadingRepo(true);
    setBundle(null);
    void repositoryArchitecture(selected.slug, controller.signal)
      .then((payload) => {
        if (current) setBundle({ crg: payload.crg, cochange: payload.cochange });
      })
      .catch(() => {
        if (current) setBundle(null);
      })
      .finally(() => {
        if (current) setLoadingRepo(false);
      });
    return () => {
      current = false;
      controller.abort();
    };
  }, [selected?.slug]);

  const communities = useMemo(() => records(tools?.communities), [tools]);
  const hubs = useMemo(() => records(tools?.hubs), [tools]);
  const bridges = useMemo(() => records(tools?.bridges), [tools]);

  async function search(text = query) {
    if (!selected || !text.trim()) return;
    setBusy(true);
    setLiveError("");
    try {
      setLive(await queryGraph(selected.slug, "semantic_search_nodes_tool", { query: text.trim(), limit: 20, detail_level: "standard" }));
    } catch (exc) {
      setLive(null);
      setLiveError(exc instanceof Error ? exc.message : "Search failed");
    } finally {
      setBusy(false);
    }
  }

  async function follow(pattern: string) {
    if (!selected || !target) return;
    setBusy(true);
    setLiveError("");
    try {
      if (pattern === "flows") {
        setLive(await queryGraph(selected.slug, "traverse_graph_tool", { query: target, mode: "bfs", depth: 2, token_budget: 2000 }));
      } else {
        setLive(await queryGraph(selected.slug, "query_graph_tool", { pattern, target, detail_level: "standard", max_results: 40 }));
      }
    } catch (exc) {
      setLive(null);
      setLiveError(exc instanceof Error ? exc.message : "The graph query failed");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    const q = params.get("q") || "";
    if (!q || !selected) return;
    const token = `${selected.slug}:${q}`;
    if (searched.current === token) return;
    searched.current = token;
    setQuery(q);
    void search(q);
  }, [params, selected?.slug]);

  if (!loaded) return <InlineLoading description="Loading architecture" />;
  if (error) return <InlineNotification kind="error" title="Architecture is unavailable" subtitle={error} lowContrast hideCloseButton />;
  if (!repos.length) {
    return (
      <NamiEmptyState
        title="No repository graphs"
        description="Refresh intelligence after repos.txt lists at least one repository."
        primaryAction={{ label: "Back to overview", href: "/" }}
      />
    );
  }

  return (
    <div className="stack-gap">
      <PageHeader
        title="Architecture"
        description="Explore how code, contracts, services and historical relationships connect across the workspace."
        breadcrumbs={service ? [{ label: "Architecture", href: "/architecture" }, { label: selected?.name || service }] : undefined}
      />
      {loadingRepo ? <SkeletonText heading width="30%" /> : null}
      {selected?.error ? <InlineNotification kind="warning" title={selected.name} subtitle={selected.error} lowContrast hideCloseButton /> : null}
      <ComboBox
        id="architecture-repo"
        titleText="Repository"
        items={repos.map((repo) => repo.name)}
        selectedItem={selected?.name}
        itemToString={(item) => item ?? ""}
        onChange={({ selectedItem }) => {
          const repo = repos.find((item) => item.name === selectedItem);
          const next = new URLSearchParams(params);
          if (repo) next.set("service", repo.slug);
          setParams(next, { replace: true });
        }}
      />
      <Tabs>
        <TabList aria-label="Architecture">
          <Tab>Structure</Tab>
          <Tab>Explore</Tab>
          <Tab>History</Tab>
          <Tab>Diagnostics</Tab>
        </TabList>
        <TabPanels>
          <TabPanel>
            <Grid>
              <Column sm={4} md={2} lg={4}><p className="stat-label"><Term term="Subsystems" definition="Groups of symbols that call each other more than they call the rest of the code." /></p><p className="stat-value">{communities.length}</p></Column>
              <Column sm={4} md={2} lg={4}><p className="stat-label"><Term term="Bridge symbols" definition="Symbols that sit between subsystems." /></p><p className="stat-value">{bridges.length}</p></Column>
              <Column sm={4} md={2} lg={4}><p className="stat-label">Highly connected symbols</p><p className="stat-value">{hubs.length}</p></Column>
              <Column sm={4} md={2} lg={4}><p className="stat-label"><Term term="Contracts" definition="HTTP, event, or RPC operations shared across services." /></p><p className="stat-value">{snapshot?.cluster?.contract_count ?? 0}</p></Column>
            </Grid>
            <h2 className="section-title">Subsystems</h2>
            <RecordTable repository={selected?.name || ""} rows={communities} emptyTitle="No subsystems" emptyDescription="This repository graph has no community summary." preferred={["repository", "symbol", "label", "size", "symbols", "cohesion"]} />
            <h2 className="section-title">Important connectors</h2>
            <NamiDataTable
              columns={[
                { key: "symbol", header: "Symbol" },
                { key: "file", header: "File" },
                { key: "role", header: "Role" },
                { key: "connections", header: "Connections" },
              ]}
              rows={connectorRows(hubs, bridges, selected?.checkout || "")}
              emptyTitle="No connectors"
              emptyDescription="No bridge or hub symbols were summarized."
              renderExpanded={(row) => (
                <dl className="detail-list">
                  <div><dt>Qualified name</dt><dd>{row.qualified}</dd></div>
                  <div><dt>Kind</dt><dd>{row.kind || "—"}</dd></div>
                </dl>
              )}
            />
            <h2 className="section-title">Boundary warnings</h2>
            <p className="helper">Connections the graph did not expect, such as a call that crosses subsystems or a test boundary. The score orders them. It is not a probability.</p>
            <NamiDataTable
              columns={[
                { key: "connection", header: "Connection" },
                { key: "relation", header: "Relation" },
                { key: "why", header: "Why" },
                { key: "score", header: "Score" },
              ]}
              rows={warningRows(tools?.surprises, selected?.checkout || "")}
              emptyTitle="No boundary warnings"
              emptyDescription="No unexpected cross-boundary connections were recorded."
              renderExpanded={(row) => (
                <dl className="detail-list">
                  <div><dt>From</dt><dd>{row.from}</dd></div>
                  <div><dt>To</dt><dd>{row.to}</dd></div>
                  {row.communities ? <div><dt>Subsystems</dt><dd>{row.communities}</dd></div> : null}
                </dl>
              )}
            />
          </TabPanel>
          <TabPanel>
            <search aria-label="Search symbols">
              <Grid>
                <Column sm={4} md={6} lg={12}>
                  <Search
                    id="symbol-search"
                    labelText="Search symbols, files, classes, functions"
                    placeholder="Search symbols, files, classes, functions"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") void search();
                    }}
                  />
                </Column>
                <Column sm={4} md={2} lg={4}>
                  <Button kind="primary" disabled={busy || !query.trim()} onClick={() => void search()}>Search</Button>
                </Column>
              </Grid>
            </search>
            {busy ? <InlineLoading description="Searching" /> : null}
            {liveError ? <InlineNotification kind="error" title="Search failed" subtitle={liveError} lowContrast hideCloseButton /> : null}
            <RecordTable
              repository={selected?.name || ""}
              rows={records(live)}
              emptyTitle={live ? "No matches" : "Search for a symbol"}
              emptyDescription={live ? "Try removing a filter or changing your search." : "Results stay in this repository."}
              preferred={["repository", "symbol", "file", "kind", "line"]}
              onUse={(row) => {
                setTarget(graphQueryTarget(row));
                setTargetFile(relativePath(String(row.file || row.file_path || "")));
              }}
            />
            {target ? (
              <div className="button-row">
                <p>Selected {repoSymbol(selected?.name || "", symbolName(target))}</p>
                {targetFile ? <p className="helper">{targetFile}</p> : null}
                {ACTIONS.map(([label, pattern]) => (
                  <Button key={pattern} kind="tertiary" size="sm" disabled={busy} onClick={() => void follow(pattern)}>{label}</Button>
                ))}
                {targetFile && selected ? <Link to={`/impact?service=${encodeURIComponent(selected.slug)}&file=${encodeURIComponent(targetFile)}`}>Explore impact</Link> : null}
              </div>
            ) : null}
          </TabPanel>
          <TabPanel>
            <h2 className="section-title">Frequently changed files</h2>
            {cochange ? (
              <NamiDataTable
                columns={[
                  { key: "code", header: "Code" },
                  { key: "file", header: "File" },
                  { key: "commits", header: "Commits" },
                  { key: "churn", header: "Edit activity" },
                ]}
                rows={(cochange.hotspots ?? []).map((item) => ({ id: item.file, code: symbolName(item.file), file: relativePath(item.file) || symbolName(item.file), commits: item.commits, churn: item.churn_score.toFixed(3) }))}
                emptyTitle="History unavailable"
                emptyDescription={cochange.message || "Commit history has not been mined for this repository."}
              />
            ) : (
              <NamiEmptyState title="History unavailable" description="Refresh intelligence to mine commit history." />
            )}
            <h2 className="section-title">Files often changed together</h2>
            <NamiDataTable
              columns={[
                { key: "pair", header: "Files" },
                { key: "commits", header: "Commits" },
                { key: "related", header: "Relationship", render: (row) => row.structural ? <StatusTag status="structure" label="Structurally related" /> : <span>Historical</span> },
              ]}
              rows={(cochange?.cochange ?? []).map((item) => ({
                id: `${item.file_a}-${item.file_b}`,
                pair: `${repoSymbol(selected?.name || "", symbolName(item.file_a))} · ${repoSymbol(selected?.name || "", symbolName(item.file_b))}`,
                commits: item.commits,
                structural: Boolean(item.is_structural),
                related: item.is_structural ? "Structurally related" : "Historical",
              }))}
              emptyTitle="No historical pairs"
              emptyDescription="No files were changed together often enough to list."
            />
          </TabPanel>
          <TabPanel>
            <p className="helper">Structural observations, not defects. They are not review findings.</p>
            <h2 className="section-title">Large functions</h2>
            <RecordTable repository={selected?.name || ""} rows={records(tools?.large_functions)} emptyTitle="No large functions" emptyDescription="No unusually large functions were recorded." preferred={["repository", "symbol", "lines"]} />
            <h2 className="section-title">Untested high-connectivity symbols</h2>
            <RecordTable repository={selected?.name || ""} rows={records(tools?.knowledge_gaps)} emptyTitle="No coverage gaps recorded" emptyDescription="The graph summary did not list untested connectors." preferred={["repository", "symbol"]} />
            <h2 className="section-title">Possible dead code</h2>
            <p className="helper">A symbol is listed when the graph finds no callers, no tests, no importers, and it is not an entry point. The reason on each row is that evidence. {limitNote(tools?.dead_code)}</p>
            <NamiDataTable
              columns={[
                { key: "symbol", header: "Symbol" },
                { key: "file", header: "File" },
                { key: "kind", header: "Kind" },
                { key: "line", header: "Line" },
                { key: "reason", header: "Reason" },
              ]}
              rows={deadCodeRows(tools?.dead_code, tools?.refactor, selected?.checkout || "")}
              emptyTitle="No possible dead code"
              emptyDescription="The graph summary did not list unused symbols."
              renderExpanded={(row) => (
                <dl className="detail-list">
                  <div><dt>Qualified name</dt><dd>{row.qualified}</dd></div>
                </dl>
              )}
            />
            <h2 className="section-title">Refactoring candidates</h2>
            <p className="helper">Each candidate names the symbol, the suggested change, and why the graph suggested it. {limitNote(tools?.refactor)}</p>
            <NamiDataTable
              columns={[
                { key: "symbol", header: "Symbol" },
                { key: "file", header: "File" },
                { key: "suggestion", header: "Suggestion" },
                { key: "reason", header: "Reason" },
              ]}
              rows={refactorRows(tools?.refactor, tools?.dead_code, selected?.checkout || "")}
              emptyTitle="No refactoring candidates"
              emptyDescription="No structural refactor suggestions were recorded."
              renderExpanded={(row) => (
                <dl className="detail-list">
                  <div><dt>Qualified name</dt><dd>{row.qualified}</dd></div>
                </dl>
              )}
            />
          </TabPanel>
        </TabPanels>
      </Tabs>
    </div>
  );
}

function Term({ term, definition }: { term: string; definition: string }) {
  return (
    <DefinitionTooltip definition={definition} openOnHover>
      <span>{term} <Information size={14} /></span>
    </DefinitionTooltip>
  );
}

function RecordTable({
  repository,
  rows,
  emptyTitle,
  emptyDescription,
  preferred,
  onUse,
}: {
  repository: string;
  rows: Record<string, unknown>[];
  emptyTitle: string;
  emptyDescription: string;
  preferred: string[];
  onUse?: (row: Record<string, string>) => void;
}) {
  const presented = rows.map((row, index) => {
    const fields = visibleFields(row, repository);
    return { id: `${fields.repository}-${fields.symbol}-${index}`, queryTarget: graphQueryTarget(row), ...fields };
  });
  const keys = preferred.filter((key) => presented.some((row) => row[key as keyof typeof row]));
  const columns = (keys.length ? keys : ["repository", "symbol"]).map((key) => ({ key, header: label(key) }));
  if (onUse) columns.push({ key: "use", header: "Use" });
  return (
    <NamiDataTable
      columns={columns.map((column) =>
        column.key === "use"
          ? { ...column, render: (row: Record<string, string>) => <Button size="sm" kind="ghost" onClick={() => onUse?.(row)}>Select</Button> }
          : column,
      )}
      rows={presented}
      emptyTitle={emptyTitle}
      emptyDescription={emptyDescription}
      renderExpanded={(row) => (
        <dl className="detail-list">
          {Object.entries(row)
            .filter(([key]) => key !== "id" && key !== "queryTarget")
            .map(([key, value]) => (
              <div key={key}>
                <dt>{label(key)}</dt>
                <dd>{value}</dd>
              </div>
            ))}
        </dl>
      )}
    />
  );
}

function records(payload: unknown): Record<string, unknown>[] {
  if (!payload) return [];
  if (Array.isArray(payload)) return payload.filter(isRecord);
  if (!isRecord(payload)) return [];
  const nested = Object.values(payload).find((value) => Array.isArray(value) && value.some(isRecord));
  return Array.isArray(nested) ? nested.filter(isRecord) : [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function label(key: string): string {
  return key.replaceAll("_", " ");
}

function connectorRows(hubs: Record<string, unknown>[], bridges: Record<string, unknown>[], checkout: string) {
  const hubsRows = hubs.map((row, index) => ({
    id: `hub-${String(row.qualified_name || row.name || index)}`,
    symbol: String(row.name || ""),
    file: displayPath(String(row.file || ""), checkout),
    role: "Highly connected",
    connections: degreeText(row),
    qualified: displayPath(String(row.qualified_name || ""), checkout),
    kind: String(row.kind || ""),
  }));
  const bridgeRows = bridges.map((row, index) => ({
    id: `bridge-${String(row.qualified_name || row.name || index)}`,
    symbol: String(row.name || ""),
    file: displayPath(String(row.file || ""), checkout),
    role: "Bridge",
    connections: betweennessText(row.betweenness),
    qualified: displayPath(String(row.qualified_name || ""), checkout),
    kind: String(row.kind || ""),
  }));
  return [...hubsRows, ...bridgeRows];
}

function warningRows(payload: unknown, checkout: string) {
  return records(payload).map((row, index) => {
    const source = String(row.source || "");
    const target = String(row.target || "");
    const reasons = Array.isArray(row.reasons) ? row.reasons.map((item) => reasonText(String(item))) : [];
    return {
      id: `${source}-${target}-${index}`,
      connection: source && target ? `${source} → ${target}` : source || target,
      relation: relationText(String(row.edge_kind || "")),
      why: reasons.join(", ") || "Unexpected connection",
      score: typeof row.surprise_score === "number" ? row.surprise_score.toFixed(3) : "—",
      from: displayPath(String(row.source_qualified || source), checkout),
      to: displayPath(String(row.target_qualified || target), checkout),
      communities: communityText(row.source_community, row.target_community),
    };
  });
}

function degreeText(row: Record<string, unknown>): string {
  const total = numberText(row.total_degree);
  const incoming = numberText(row.in_degree);
  const outgoing = numberText(row.out_degree);
  if (!total) return "Degree unavailable";
  return `${total} total · ${incoming || "0"} in · ${outgoing || "0"} out`;
}

function betweennessText(value: unknown): string {
  return typeof value === "number" ? `${value.toFixed(3)} betweenness` : "Betweenness unavailable";
}

function numberText(value: unknown): string {
  return typeof value === "number" ? String(value) : "";
}

function communityText(source: unknown, target: unknown): string {
  if (typeof source !== "number" && typeof target !== "number") return "";
  return `${source ?? "—"} → ${target ?? "—"}`;
}

function displayPath(value: string, checkout: string): string {
  const trimmed = value.trim().replaceAll("\\", "/");
  if (!trimmed) return "";
  const root = checkout.trim().replaceAll("\\", "/").replace(/\/$/, "");
  const [filePart, symbol] = trimmed.split("::");
  let file = filePart;
  if (root && file.startsWith(`${root}/`)) file = file.slice(root.length + 1);
  if (file.startsWith("/")) file = "";
  if (symbol) return file ? `${file}::${symbol}` : symbol;
  return file;
}

function relationText(relation: string): string {
  const labels: Record<string, string> = {
    CALLS: "calls",
    IMPORTS_FROM: "imports",
    HANDLED_BY: "handled by",
    IMPLEMENTED_BY: "implemented by",
    CONSUMES_HTTP: "consumes HTTP endpoint",
    CONSUMES_GRPC: "consumes gRPC operation",
    PRODUCES_EVENT: "produces event",
    CONSUMED_BY: "consumed by",
    TESTED_BY: "tested by",
  };
  return labels[relation] || relation.replaceAll("_", " ").toLowerCase() || "related";
}

function reasonText(reason: string): string {
  const labels: Record<string, string> = {
    "cross-community": "Crosses subsystems",
    "cross-test-boundary": "Crosses a test boundary",
  };
  return labels[reason] || reason.replaceAll("-", " ");
}

function deadCodeRows(deadPayload: unknown, refactorPayload: unknown, checkout: string) {
  const reasons = new Map<string, string>();
  for (const suggestion of records(refactorPayload)) {
    const rationale = String(suggestion.rationale || "");
    for (const symbol of stringList(suggestion.symbols)) {
      if (rationale) reasons.set(symbol, rationale);
    }
  }
  return records(deadPayload).map((row, index) => {
    const qualified = String(row.qualified_name || "");
    return {
      id: qualified || `dead-${index}`,
      symbol: symbolLabel(row),
      file: relativeFile(row, checkout),
      kind: String(row.kind || ""),
      line: row.line ? String(row.line) : "",
      reason: reasons.get(qualified) || "The graph listed this symbol as unused. No separate reason was recorded.",
      qualified: displayPath(qualified, checkout) || symbolLabel(row),
    };
  });
}

function refactorRows(refactorPayload: unknown, deadPayload: unknown, checkout: string) {
  const located = new Map(records(deadPayload).map((row) => [String(row.qualified_name || ""), row]));
  return records(refactorPayload).flatMap((suggestion, index) => {
    const symbols = stringList(suggestion.symbols);
    const targets = symbols.length ? symbols : [""];
    return targets.map((qualified, offset) => {
      const locatedRow = located.get(qualified);
      return {
        id: `${qualified || "refactor"}-${index}-${offset}`,
        symbol: symbolLabel(locatedRow || { qualified_name: qualified, name: quotedName(String(suggestion.description || "")) }),
        file: locatedRow ? relativeFile(locatedRow, checkout) : fileFromQualified(qualified, checkout),
        suggestion: suggestionText(suggestion),
        reason: String(suggestion.rationale || "No reason was recorded."),
        qualified: displayPath(qualified, checkout),
      };
    });
  });
}

function limitNote(payload: unknown): string {
  if (!isRecord(payload) || payload.truncated !== true) return "";
  const total = typeof payload.total === "number" ? payload.total : 0;
  const shown = records(payload).length;
  return total > shown ? `Showing ${shown} of ${total}.` : "";
}

function symbolLabel(row: Record<string, unknown>): string {
  const qualified = String(row.qualified_name || "");
  const after = qualified.split("::").pop() || "";
  if (after && !after.includes("/")) return after;
  return String(row.name || after);
}

function relativeFile(row: Record<string, unknown>, checkout: string): string {
  const relative = String(row.relative_path || "");
  if (relative && !relative.startsWith("/") && !relative.split("/").includes("..")) return relative;
  return fileFromQualified(String(row.qualified_name || row.file || row.file_path || ""), checkout);
}

function fileFromQualified(qualified: string, checkout: string): string {
  const shown = displayPath(qualified, checkout);
  if (shown.includes("::")) return shown.split("::")[0];
  return shown.includes("/") ? shown : "";
}

function suggestionText(suggestion: Record<string, unknown>): string {
  const description = String(suggestion.description || "").replace(/ '[^']*'$/, "").trim();
  if (description) return description;
  const type = String(suggestion.type || "").replaceAll("_", " ");
  return type ? type.charAt(0).toUpperCase() + type.slice(1) : "Review this symbol";
}

function quotedName(description: string): string {
  return description.match(/'([^']+)'/)?.[1] || "";
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.length > 0) : [];
}
