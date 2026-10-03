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
const LABELS: Record<string, string> = {
  community_id: "Group",
  source_community: "From group",
  target_community: "To group",
  cohesion: "Group tightness",
  criticality: "Dependence",
  betweenness: "Bridge role",
  total_degree: "Connections",
  in_degree: "Callers",
  out_degree: "Calls out",
  size: "Symbols",
  node_count: "Symbols on the path",
  file_count: "Files on the path",
  risk_score: "Change risk",
  coupling: "How often edited together",
  churn_score: "Edit activity",
  commits: "Commits",
  authors: "People",
  dominant_language: "Main language",
};
const GLOSSARY = [
  ["Group", "A community: symbols that call each other more than they call the rest of the code. Shown by name."],
  ["Path", "A flow: one route from an entry point, such as a handler or a test, through the functions it calls."],
  ["Dependence", "How much of the program sits on that path. High, medium, or low."],
  ["Group tightness", "Whether the symbols in a group mostly call each other. Tight, mixed, or loose."],
  ["Bridge role", "Whether a symbol sits between groups. Chokepoint, link, or local."],
  ["Connections", "How many calls and imports touch a symbol."],
].map(([term, meaning]) => `${term}: ${meaning}`).join(" ");
const PATH_COLUMNS = new Set(["qualified_name", "source_qualified", "target_qualified", "file_path", "relative_path"]);

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
  const names = communityNames(tools);
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
          <p>Groups, paths, and connections in one repository. Scores are named so a table can be read without the raw figures.</p>
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
      <InlineNotification
        kind="info"
        title="How to read this graph"
        subtitle={GLOSSARY}
        lowContrast
        hideCloseButton
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
            <Hint>
              A snapshot of this repository: how many files and symbols the graph contains, how risky the latest changes look, and review questions worth asking.
            </Hint>
            <PayloadView payload={tools?.stats ?? null} names={names} />
            <PayloadView payload={tools?.minimal ?? null} names={names} />
            <PayloadView payload={tools?.questions ?? null} names={names} />
          </TabPanel>
          <TabPanel>
            <Hint>
              Search finds a function, class, or file by name. Query shows one relationship, such as who calls a symbol or what it imports. Explore walks outward from the best match.
            </Hint>
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
            <LiveResult tab="search" liveTab={liveTab} payload={live} error={liveError} names={names} />
          </TabPanel>
          <TabPanel>
            <Hint>
              A flow is one path the program takes from an entry point, such as an HTTP handler or a test, through the functions it calls. Higher criticality means more of the system depends on it.
            </Hint>
            <PayloadView payload={tools?.flows ?? null} names={names} />
            <FlowLookup slug={selected?.slug} busy={busy} onAsk={(tool, args) => void ask("flows", tool, args)} />
            <LiveResult tab="flows" liveTab={liveTab} payload={live} error={liveError} names={names} />
          </TabPanel>
          <TabPanel>
            <Hint>
              A community is a cluster of symbols that call each other more than they call the rest of the code. It usually lines up with a feature or a folder.
            </Hint>
            <PayloadView payload={tools?.communities ?? null} names={names} />
            <CommunityLookup slug={selected?.slug} busy={busy} onAsk={(tool, args) => void ask("communities", tool, args)} />
            <LiveResult tab="communities" liveTab={liveTab} payload={live} error={liveError} names={names} />
          </TabPanel>
          <TabPanel>
            <Hint>
              A hub has many connections, so a change there spreads widely. A bridge sits between communities. If it breaks, those areas lose their link to each other.
            </Hint>
            <PayloadView payload={tools?.hubs ?? null} names={names} />
            <PayloadView payload={tools?.bridges ?? null} names={names} />
          </TabPanel>
          <TabPanel>
            <Hint>
              Impact is the set of symbols reached by following calls and imports out from a changed file. Affected flows are the entry-point paths that pass through that file.
            </Hint>
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
            <LiveResult tab="impact" liveTab={liveTab} payload={live} error={liveError} names={names} />
          </TabPanel>
          <TabPanel>
            <Hint>
              Architecture is the map of communities and the links between them. A warning means two areas are coupled more tightly than their boundary suggests.
            </Hint>
            <PayloadView payload={tools?.architecture ?? null} names={names} />
          </TabPanel>
          <TabPanel>
            <Hint>
              Quality lists oversized files, symbols with no connections, and busy symbols that have no test. These are structural weak spots, not style nits.
            </Hint>
            <PayloadView payload={tools?.large_functions ?? null} names={names} />
            <PayloadView payload={tools?.knowledge_gaps ?? null} names={names} />
            <PayloadView payload={tools?.dead_code ?? null} names={names} />
          </TabPanel>
          <TabPanel>
            <Hint>
              Refactor suggestions say which symbols look unused or misplaced in their community. The dashboard only reports them. It does not edit the repository.
            </Hint>
            <PayloadView payload={tools?.refactor ?? null} names={names} />
          </TabPanel>
          <TabPanel>
            <Hint>
              Coupling lists symbol pairs whose connection is unexpected, for example across communities, languages, or the boundary between production code and tests.
            </Hint>
            <PayloadView payload={tools?.surprises ?? null} names={names} />
          </TabPanel>
          <TabPanel>
            <Hint>
              Changes scores the latest git diff: which functions moved, which flows those functions sit on, and which of them have no test.
            </Hint>
            <PayloadView payload={tools?.changes ?? null} names={names} />
          </TabPanel>
          <TabPanel>
            <Hint>
              Hotspots are files that git history shows changing often, or changing together. A high coupling score means two files tend to be edited in the same commits.
            </Hint>
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
              <RecordTable rows={cochange?.hotspots ?? []} names={names} />
              <RecordTable rows={cochange?.cochange ?? []} names={names} />
            </div>
          </TabPanel>
        </TabPanels>
      </Tabs>
    </div>
  );
}

function Hint({ children }: { children: string }) {
  return <InlineNotification kind="info" title="What this tab shows" subtitle={children} lowContrast hideCloseButton />;
}

function communityNames(tools: Record<string, CrgPayload> | undefined): Record<string, string> {
  const names: Record<string, string> = {};
  for (const payload of Object.values(tools ?? {})) {
    collectNames(payload, names);
  }
  return names;
}

function collectNames(value: unknown, names: Record<string, string>) {
  if (Array.isArray(value)) {
    for (const item of value) collectNames(item, names);
    return;
  }
  if (!isRecord(value)) return;
  const id = value.id ?? value.community_id;
  const name = typeof value.name === "string" ? value.name : "";
  if ((typeof id === "number" || typeof id === "string") && name) names[String(id)] = name;
  for (const child of Object.values(value)) collectNames(child, names);
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

function LiveResult({
  tab,
  liveTab,
  payload,
  error,
  names,
}: {
  tab: string;
  liveTab: string;
  payload: CrgPayload | null;
  error: string;
  names: Record<string, string>;
}) {
  if (tab !== liveTab) return null;
  return (
    <>
      {error ? <InlineNotification kind="error" title="Graph query" subtitle={error} lowContrast hideCloseButton /> : null}
      <PayloadView payload={payload} names={names} />
    </>
  );
}

function repoCount(stats: GraphRepo["stats"]): string {
  const record = stats as { files_count?: number; files?: number; total_nodes?: number } | null | undefined;
  if (!record) return "no stats";
  if (typeof record.files_count === "number") return `${record.files_count} files, ${record.total_nodes ?? 0} symbols`;
  if (typeof record.files === "number") return `${record.files} files`;
  return "no stats";
}

function PayloadView({ payload, names }: { payload: CrgPayload | null; names: Record<string, string> }) {
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
      {typeof summary === "string" && summary ? <p className="stat-label">{symbolText(summary)}</p> : null}
      {isRecord(summary) ? <RecordTable rows={[summary]} names={names} /> : null}
      {warnings.map((warning) => (
        <InlineNotification key={warning} kind="warning" title="Graph" subtitle={warning} lowContrast hideCloseButton />
      ))}
      {facts.length ? <RecordTable rows={[Object.fromEntries(facts)]} names={names} /> : null}
      {tables.map(([key, value]) => (
        <section key={key}>
          <p className="stat-label">{label(key)}</p>
          <RecordTable rows={(value as unknown[]).filter(isRecord)} names={names} />
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
                <RecordTable rows={rows.filter(isRecord)} names={names} />
              </div>
            ) : null,
          )}
        </section>
      ))}
    </div>
  );
}

function RecordTable({ rows, names = {} }: { rows: Record<string, unknown>[]; names?: Record<string, string> }) {
  if (!rows.length) return <p className="stat-label">None</p>;
  const keys: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (key.startsWith("_") || keys.includes(key)) continue;
      keys.push(key);
    }
  }
  const hasSymbol = rows.some((row) => "name" in row || "source" in row);
  const visible = keys
    .filter((key) => !PATH_COLUMNS.has(key) && !(key === "file" && hasSymbol))
    .filter((key) => rows.some((row) => !isRecord(row[key]) || Array.isArray(row[key])))
    .slice(0, 8);
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
                <TableCell key={key}>{cell(key, row[key], names)}</TableCell>
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

function cell(key: string, value: unknown, names: Record<string, string>): string {
  if (value == null || value === "") return "—";
  if (key === "community_id" || key.endsWith("_community")) {
    const named = names[String(value)];
    if (named) return named;
  }
  if (typeof value === "number") return namedNumber(key, value);
  if (typeof value === "string") return symbolText(value);
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (Array.isArray(value)) return value.map((item) => cell(key, item, names)).filter(Boolean).join(", ");
  return symbolText(JSON.stringify(value));
}

function namedNumber(key: string, value: number): string {
  if (key === "cohesion") return value >= 0.2 ? "Tight" : value >= 0.08 ? "Mixed" : "Loose";
  if (key === "criticality" || key === "risk_score") return value >= 0.6 ? "High" : value >= 0.25 ? "Medium" : "Low";
  if (key === "betweenness") return value >= 0.05 ? "Chokepoint" : value > 0 ? "Link" : "Local";
  if (key === "coupling") return value >= 0.6 ? "Often together" : value >= 0.25 ? "Sometimes together" : "Rarely together";
  if (key === "size" || key === "node_count") return value === 1 ? "1 symbol" : `${value} symbols`;
  if (key === "file_count" || key === "files" || key === "files_count") return value === 1 ? "1 file" : `${value} files`;
  if (key === "total_degree") return value === 1 ? "1 connection" : `${value} connections`;
  if (key === "in_degree") return value === 1 ? "1 caller" : `${value} callers`;
  if (key === "out_degree") return value === 1 ? "1 call out" : `${value} calls out`;
  if (key === "commits") return value === 1 ? "1 commit" : `${value} commits`;
  if (key === "line" || key === "line_start" || key === "lines" || key === "line_count") return `line ${value}`;
  return String(value);
}

function symbolText(value: string): string {
  return value
    .split(/(\s+)/)
    .map((piece) => {
      if (piece.includes("::")) return piece.slice(piece.lastIndexOf("::") + 2);
      if (piece.startsWith("/")) return piece.slice(piece.lastIndexOf("/") + 1).replace(/[),.;:]+$/, "");
      return piece;
    })
    .join("")
    .replace(/\s+\([^)\n]*\/[^)\n]*\)/g, "");
}

function label(key: string): string {
  return LABELS[key] ?? key.replaceAll("_", " ");
}
