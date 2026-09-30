import { useState } from "react";
import {
  Button,
  Dropdown,
  InlineNotification,
  Tab,
  TabList,
  TabPanel,
  TabPanels,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Tabs,
  TextInput,
} from "@carbon/react";
import { Renew, Search } from "@carbon/icons-react";
import { queryGraph, type CrgPayload, type GraphRepo, type Snapshot } from "../api";

const KINDS = ["Any", "File", "Class", "Function", "Type", "Test"];
const PATTERNS = [
  "callers_of",
  "callees_of",
  "references_to",
  "imports_of",
  "importers_of",
  "children_of",
  "tests_for",
  "inheritors_of",
  "file_summary",
];

const SKIP = new Set(["_hints", "_graph", "context_savings", "next_tool_suggestions"]);

export function GraphPage({
  snapshot,
  error,
  loaded,
  onBuild,
}: {
  snapshot: Snapshot | null;
  error: string;
  loaded: boolean;
  onBuild: () => void;
}) {
  const repos = snapshot?.graphs?.repos ?? [];
  const [slug, setSlug] = useState(repos[0]?.slug ?? "");
  const selected = repos.find((repo) => repo.slug === slug) ?? repos[0];
  const tools = selected ? snapshot?.graph_details[selected.slug]?.crg?.tools : undefined;
  const cochange = selected ? snapshot?.graph_details[selected.slug]?.cochange : undefined;
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("Any");
  const [pattern, setPattern] = useState(PATTERNS[0]);
  const [target, setTarget] = useState("");
  const [impactFile, setImpactFile] = useState("");
  const [live, setLive] = useState<CrgPayload | null>(null);
  const [liveTab, setLiveTab] = useState("");
  const [liveError, setLiveError] = useState("");
  const [busy, setBusy] = useState(false);

  async function ask(tab: string, tool: string, args: Record<string, unknown>) {
    setLiveTab(tab);
    if (!selected) return;
    setBusy(true);
    setLiveError("");
    try {
      setLive(await queryGraph(selected.slug, tool, args));
    } catch (exc) {
      setLive(null);
      setLiveError(exc instanceof Error ? exc.message : "Graph query failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack-gap">
      <div className="page-head">
        <div>
          <h1>Code graph</h1>
          <p>Communities, flows, impact, and quality come from the code-review-graph MCP server.</p>
        </div>
        <Button kind="secondary" renderIcon={Renew} onClick={onBuild}>
          Build graphs
        </Button>
      </div>
      {error ? <InlineNotification kind="error" title="API" subtitle={error} lowContrast hideCloseButton /> : null}
      {selected?.error ? (
        <InlineNotification kind="warning" title={selected.name} subtitle={selected.error} lowContrast hideCloseButton />
      ) : null}
      {!loaded || repos.length ? null : (
        <InlineNotification
          kind="info"
          title="No graphs"
          subtitle="Build graphs after repos.txt has at least one repository."
          lowContrast
          hideCloseButton
        />
      )}
      <Dropdown
        id="graph-repo"
        titleText="Repository"
        label="Choose a repository"
        items={repos}
        itemToString={(item) => (item ? `${item.name} (${repoCount(item.stats)})` : "")}
        selectedItem={selected ?? null}
        onChange={({ selectedItem }) => {
          setSlug(selectedItem?.slug ?? "");
          setLive(null);
          setLiveError("");
        }}
      />
      <Tabs>
        <TabList aria-label="Code graph views" contained>
          <Tab>Overview</Tab>
          <Tab>Search</Tab>
          <Tab>Flows</Tab>
          <Tab>Communities</Tab>
          <Tab>Hubs and bridges</Tab>
          <Tab>Impact</Tab>
          <Tab>Architecture</Tab>
          <Tab>Quality</Tab>
          <Tab>Refactor</Tab>
          <Tab>Coupling</Tab>
          <Tab>Changes</Tab>
          <Tab>Hotspots</Tab>
        </TabList>
        <TabPanels>
          <TabPanel>
            <PayloadView payload={tools?.stats ?? null} />
            <PayloadView payload={tools?.minimal ?? null} />
            <PayloadView payload={tools?.questions ?? null} />
          </TabPanel>
          <TabPanel>
            <div className="graph-toolbar">
              <TextInput
                id="symbol-search"
                labelText="Search"
                placeholder="function or file"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <Dropdown
                id="search-kind"
                titleText="Kind"
                label="Any"
                items={KINDS}
                itemToString={(item) => item ?? ""}
                selectedItem={kind}
                onChange={({ selectedItem }) => setKind(selectedItem ?? "Any")}
              />
              <Button
                kind="primary"
                renderIcon={Search}
                disabled={busy || !query.trim()}
                onClick={() =>
                  void ask("search", "semantic_search_nodes_tool", {
                    query: query.trim(),
                    kind: kind === "Any" ? null : kind,
                    limit: 20,
                    detail_level: "standard",
                  })
                }
              >
                Search
              </Button>
            </div>
            <div className="graph-toolbar">
              <Dropdown
                id="query-pattern"
                titleText="Relationship"
                label="Pattern"
                items={PATTERNS}
                itemToString={(item) => item ?? ""}
                selectedItem={pattern}
                onChange={({ selectedItem }) => setPattern(selectedItem ?? PATTERNS[0])}
              />
              <TextInput
                id="query-target"
                labelText="Target"
                placeholder="symbol or file"
                value={target}
                onChange={(event) => setTarget(event.target.value)}
              />
              <div className="button-row">
                <Button kind="secondary" disabled={busy || !target.trim()} onClick={() => void ask("search", "query_graph_tool", { pattern, target: target.trim(), detail_level: "standard", max_results: 40 })}>
                  Query
                </Button>
                <Button kind="tertiary" disabled={busy || !query.trim()} onClick={() => void ask("search", "traverse_graph_tool", { query: query.trim(), mode: "bfs", depth: 2, token_budget: 2000 })}>
                  Explore
                </Button>
              </div>
            </div>
            <LiveResult tab="search" liveTab={liveTab} payload={live} error={liveError} />
          </TabPanel>
          <TabPanel>
            <PayloadView payload={tools?.flows ?? null} />
            <FlowLookup slug={selected?.slug} busy={busy} onAsk={(tool, args) => void ask("flows", tool, args)} />
            <LiveResult tab="flows" liveTab={liveTab} payload={live} error={liveError} />
          </TabPanel>
          <TabPanel>
            <PayloadView payload={tools?.communities ?? null} />
            <CommunityLookup slug={selected?.slug} busy={busy} onAsk={(tool, args) => void ask("communities", tool, args)} />
            <LiveResult tab="communities" liveTab={liveTab} payload={live} error={liveError} />
          </TabPanel>
          <TabPanel>
            <PayloadView payload={tools?.hubs ?? null} />
            <PayloadView payload={tools?.bridges ?? null} />
          </TabPanel>
          <TabPanel>
            <div className="graph-toolbar">
              <TextInput
                id="impact-file"
                labelText="Changed file"
                placeholder="app/main.py"
                value={impactFile}
                onChange={(event) => setImpactFile(event.target.value)}
              />
              <Button
                kind="primary"
                disabled={busy || !impactFile.trim()}
                onClick={() =>
                  void ask("impact", "get_impact_radius_tool", {
                    changed_files: [impactFile.trim()],
                    max_depth: 2,
                    detail_level: "standard",
                  })
                }
              >
                Impact
              </Button>
              <Button
                kind="tertiary"
                disabled={busy || !impactFile.trim()}
                onClick={() => void ask("impact", "get_affected_flows_tool", { changed_files: [impactFile.trim()], detail_level: "minimal", max_flows: 20 })}
              >
                Affected flows
              </Button>
            </div>
            <LiveResult tab="impact" liveTab={liveTab} payload={live} error={liveError} />
          </TabPanel>
          <TabPanel>
            <PayloadView payload={tools?.architecture ?? null} />
          </TabPanel>
          <TabPanel>
            <PayloadView payload={tools?.large_functions ?? null} />
            <PayloadView payload={tools?.knowledge_gaps ?? null} />
            <PayloadView payload={tools?.dead_code ?? null} />
          </TabPanel>
          <TabPanel>
            <PayloadView payload={tools?.refactor ?? null} />
          </TabPanel>
          <TabPanel>
            <PayloadView payload={tools?.surprises ?? null} />
          </TabPanel>
          <TabPanel>
            <PayloadView payload={tools?.changes ?? null} />
          </TabPanel>
          <TabPanel>
            <div className="stack-gap">
              {cochange?.message ? (
                <InlineNotification kind="warning" title="History" subtitle={cochange.message} lowContrast hideCloseButton />
              ) : (
                <p className="stat-label">
                  {cochange?.commit_count ?? 0} commits, {cochange?.author_count ?? 0}{" "}
                  {(cochange?.author_count ?? 0) === 1 ? "author" : "authors"}, window {cochange?.window_label || "—"}.
                  Coupling here is from commit history. Graph coupling is on the Coupling tab.
                </p>
              )}
              <RecordTable rows={cochange?.hotspots ?? []} />
              <RecordTable rows={cochange?.cochange ?? []} />
            </div>
          </TabPanel>
        </TabPanels>
      </Tabs>
    </div>
  );
}

function FlowLookup({
  slug,
  busy,
  onAsk,
}: {
  slug?: string;
  busy: boolean;
  onAsk: (tool: string, args: Record<string, unknown>) => void;
}) {
  const [name, setName] = useState("");
  if (!slug) return null;
  return (
    <div className="graph-toolbar">
      <TextInput id="flow-name" labelText="Flow" placeholder="flow name" value={name} onChange={(event) => setName(event.target.value)} />
      <Button kind="secondary" disabled={busy || !name.trim()} onClick={() => onAsk("get_flow_tool", { flow_name: name.trim(), include_source: false, max_steps: 40 })}>
        Open flow
      </Button>
    </div>
  );
}

function CommunityLookup({
  slug,
  busy,
  onAsk,
}: {
  slug?: string;
  busy: boolean;
  onAsk: (tool: string, args: Record<string, unknown>) => void;
}) {
  const [name, setName] = useState("");
  if (!slug) return null;
  return (
    <div className="graph-toolbar">
      <TextInput id="community-name" labelText="Community" placeholder="community name" value={name} onChange={(event) => setName(event.target.value)} />
      <Button
        kind="secondary"
        disabled={busy || !name.trim()}
        onClick={() => onAsk("get_community_tool", { community_name: name.trim(), include_members: true, max_members: 40 })}
      >
        Open community
      </Button>
    </div>
  );
}

function LiveResult({ tab, liveTab, payload, error }: { tab: string; liveTab: string; payload: CrgPayload | null; error: string }) {
  if (tab !== liveTab) return null;
  return (
    <>
      {error ? <InlineNotification kind="error" title="Graph query" subtitle={error} lowContrast hideCloseButton /> : null}
      <PayloadView payload={payload} />
    </>
  );
}

function repoCount(stats: GraphRepo["stats"]): string {
  const record = stats as { files_count?: number; files?: number; total_nodes?: number } | null | undefined;
  if (!record) return "no stats";
  if (typeof record.files_count === "number") return `${record.files_count} files, ${record.total_nodes ?? 0} nodes`;
  if (typeof record.files === "number") return `${record.files} files`;
  return "no stats";
}

function PayloadView({ payload }: { payload: CrgPayload | null }) {
  if (!payload) return null;
  const summary = payload.summary;
  const warnings = Array.isArray(payload.warnings) ? payload.warnings.filter((item): item is string => typeof item === "string") : [];
  const tables = Object.entries(payload).filter(([key, value]) => !SKIP.has(key) && Array.isArray(value) && value.some(isRecord));
  const nested = Object.entries(payload).filter((entry): entry is [string, Record<string, unknown>] => {
    const [key, value] = entry;
    return !SKIP.has(key) && key !== "summary" && isRecord(value) && Object.values(value).some((item) => Array.isArray(item));
  });
  const facts = Object.entries(payload).filter(
    ([key, value]) => !SKIP.has(key) && key !== "summary" && (typeof value === "string" || typeof value === "number" || typeof value === "boolean"),
  );
  return (
    <div className="stack-gap payload-block">
      {typeof summary === "string" && summary ? <p className="stat-label">{summary}</p> : null}
      {isRecord(summary) ? <RecordTable rows={[summary]} /> : null}
      {warnings.map((warning) => (
        <InlineNotification key={warning} kind="warning" title="Graph" subtitle={warning} lowContrast hideCloseButton />
      ))}
      {facts.length ? <RecordTable rows={[Object.fromEntries(facts)]} /> : null}
      {tables.map(([key, value]) => (
        <section key={key}>
          <p className="stat-label">{label(key)}</p>
          <RecordTable rows={(value as unknown[]).filter(isRecord)} />
        </section>
      ))}
      {nested.map(([key, value]) => (
        <section key={key}>
          {Object.entries(value).map(([child, rows]) =>
            Array.isArray(rows) ? (
              <div key={child} className="stack-gap">
                <p className="stat-label">
                  {label(key)} / {label(child)}
                </p>
                <RecordTable rows={rows.filter(isRecord)} />
              </div>
            ) : null,
          )}
        </section>
      ))}
    </div>
  );
}

function RecordTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows.length) return <p className="stat-label">None</p>;
  const keys: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (key.startsWith("_") || keys.includes(key)) continue;
      keys.push(key);
    }
  }
  const visible = keys.filter((key) => rows.some((row) => !isRecord(row[key]) || Array.isArray(row[key]))).slice(0, 8);
  return (
    <div className="table-scroll">
      <Table size="lg" useZebraStyles>
        <TableHead>
          <TableRow>
            {visible.map((key) => (
              <TableHeader key={key}>{label(key)}</TableHeader>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((row, index) => (
            <TableRow key={String(row.id ?? row.name ?? row.file ?? index)}>
              {visible.map((key) => (
                <TableCell key={key}>{cell(row[key])}</TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function cell(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map((item) => cell(item)).filter(Boolean).join(", ");
  return JSON.stringify(value);
}

function label(key: string): string {
  return key.replaceAll("_", " ");
}
