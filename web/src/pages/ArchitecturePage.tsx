import { useEffect, useMemo, useState } from "react";
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
  Tab,
  TabList,
  TabPanel,
  TabPanels,
  Tabs,
} from "@carbon/react";
import { queryGraph, type CrgPayload, type Snapshot } from "../api";
import { NamiDataTable } from "../components/NamiDataTable";
import { NamiEmptyState } from "../components/NamiEmptyState";
import { PageHeader } from "../components/PageHeader";
import { StatusTag } from "../components/StatusTag";
import { repoSymbol, symbolName, visibleFields } from "../labels";

const ACTIONS = [
  ["Callers", "callers_of"],
  ["Calls", "callees_of"],
  ["Imports", "imports_of"],
  ["References", "references_to"],
  ["Tests", "tests_for"],
  ["Flows", "flows"],
] as const;

export function ArchitecturePage({ snapshot, loaded, error }: { snapshot: Snapshot | null; loaded: boolean; error: string }) {
  const [params, setParams] = useSearchParams();
  const repos = snapshot?.graphs?.repos ?? [];
  const service = params.get("service") || "";
  const selected = repos.find((repo) => repo.slug === service || repo.name === service) ?? repos[0];
  const tools = selected ? snapshot?.graph_details[selected.slug]?.crg?.tools : undefined;
  const cochange = selected ? snapshot?.graph_details[selected.slug]?.cochange : undefined;
  const [query, setQuery] = useState(params.get("q") || "");
  const [target, setTarget] = useState("");
  const [live, setLive] = useState<CrgPayload | null>(null);
  const [liveError, setLiveError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (params.get("q")) setQuery(params.get("q") || "");
  }, [params]);

  const communities = useMemo(() => records(tools?.communities), [tools]);
  const hubs = useMemo(() => records(tools?.hubs), [tools]);
  const bridges = useMemo(() => records(tools?.bridges), [tools]);

  async function search() {
    if (!selected) return;
    setBusy(true);
    setLiveError("");
    try {
      setLive(await queryGraph(selected.slug, "semantic_search_nodes_tool", { query: query.trim(), limit: 20, detail_level: "standard" }));
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
            <RecordTable repository={selected?.name || ""} rows={[...bridges, ...hubs]} emptyTitle="No connectors" emptyDescription="No bridge or hub symbols were summarized." preferred={["repository", "symbol", "betweenness", "criticality", "total_degree"]} />
            <h2 className="section-title">Boundary warnings</h2>
            <RecordTable repository={selected?.name || ""} rows={records(tools?.surprises)} emptyTitle="No boundary warnings" emptyDescription="No architecture boundary surprises were recorded." preferred={["repository", "symbol", "message", "kind"]} />
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
              preferred={["repository", "symbol", "kind", "line"]}
              onUse={(value) => setTarget(value)}
            />
            {target ? (
              <div className="button-row">
                <p>Selected {repoSymbol(selected?.name || "", target)}</p>
                {ACTIONS.map(([label, pattern]) => (
                  <Button key={pattern} kind="tertiary" size="sm" disabled={busy} onClick={() => void follow(pattern)}>{label}</Button>
                ))}
              </div>
            ) : null}
          </TabPanel>
          <TabPanel>
            <h2 className="section-title">Frequently changed files</h2>
            {cochange ? (
              <NamiDataTable
                columns={[
                  { key: "code", header: "Code" },
                  { key: "commits", header: "Commits" },
                  { key: "churn", header: "Edit activity" },
                ]}
                rows={(cochange.hotspots ?? []).map((item) => ({ id: item.file, code: repoSymbol(selected?.name || "", symbolName(item.file)), commits: item.commits, churn: item.churn_score.toFixed(3) }))}
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
            <RecordTable repository={selected?.name || ""} rows={records(tools?.dead_code)} emptyTitle="No possible dead code" emptyDescription="The graph summary did not list unused symbols." preferred={["repository", "symbol"]} />
            <h2 className="section-title">Refactoring candidates</h2>
            <RecordTable repository={selected?.name || ""} rows={records(tools?.refactor)} emptyTitle="No refactoring candidates" emptyDescription="No structural refactor suggestions were recorded." preferred={["repository", "symbol", "message", "kind"]} />
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
  onUse?: (value: string) => void;
}) {
  const presented = rows.map((row, index) => {
    const fields = visibleFields(row, repository);
    const symbol = String(row.name || row.qualified_name || fields.symbol || index);
    return { id: `${fields.repository}-${fields.symbol}-${index}`, queryTarget: symbolName(symbol), ...fields };
  });
  const keys = preferred.filter((key) => presented.some((row) => row[key as keyof typeof row]));
  const columns = (keys.length ? keys : ["repository", "symbol"]).map((key) => ({ key, header: label(key) }));
  if (onUse) columns.push({ key: "use", header: "Use" });
  return (
    <NamiDataTable
      columns={columns.map((column) =>
        column.key === "use"
          ? { ...column, render: (row: { queryTarget?: string }) => <Button size="sm" kind="ghost" onClick={() => onUse?.(row.queryTarget || "")}>Select</Button> }
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
