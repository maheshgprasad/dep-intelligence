import { useMemo, useState } from "react";
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
  Tag,
  TextInput,
} from "@carbon/react";
import { Renew } from "@carbon/icons-react";
import type { ImportGraph, Snapshot } from "../api";

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
  const detail = selected ? snapshot?.graph_details[selected.slug] : undefined;
  const graph = detail?.graph ?? null;
  const cochange = detail?.cochange ?? null;
  const [query, setQuery] = useState("");
  const [impactSeed, setImpactSeed] = useState("");

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!graph || !needle) return graph?.nodes ?? [];
    return graph.nodes.filter((node) => node.id.toLowerCase().includes(needle));
  }, [graph, query]);

  const impact = useMemo(() => (graph ? blast(graph, impactSeed) : []), [graph, impactSeed]);

  return (
    <div className="stack-gap">
      <div className="page-head">
        <div>
          <h1>Code graph</h1>
          <p>Import structure, hotspots, and hidden co-change. This view does not call code-review-graph.</p>
        </div>
        <Button kind="secondary" renderIcon={Renew} onClick={onBuild}>
          Build graphs
        </Button>
      </div>
      {error ? <InlineNotification kind="error" title="API" subtitle={error} lowContrast hideCloseButton /> : null}
      {!loaded || repos.length ? null : (
        <InlineNotification
          kind="info"
          title="No graphs"
          subtitle="Run analysis, or build graphs, after repos.txt has at least one repository."
          lowContrast
          hideCloseButton
        />
      )}
      {graph?.note ? (
        <InlineNotification
          kind="info"
          title="Import graph"
          subtitle="Built from import and require edges. Execution flows from code-review-graph are not part of this view."
          lowContrast
          hideCloseButton
        />
      ) : null}
      <div className="graph-toolbar">
        <Dropdown
          id="graph-repo"
          titleText="Repository"
          label="Choose a repository"
          items={repos}
          itemToString={(item) => (item ? `${item.name} (${item.stats.files} files)` : "")}
          selectedItem={selected ?? null}
          onChange={({ selectedItem }) => setSlug(selectedItem?.slug ?? "")}
        />
        <TextInput
          id="symbol-search"
          labelText="Search files"
          placeholder="src/users.js"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <TextInput
          id="impact-seed"
          labelText="Impact from file"
          placeholder="src/server.js"
          value={impactSeed}
          onChange={(event) => setImpactSeed(event.target.value)}
        />
      </div>
      <Tabs>
        <TabList aria-label="Code graph views" contained>
          <Tab>Search</Tab>
          <Tab>Communities</Tab>
          <Tab>Hubs and bridges</Tab>
          <Tab>Impact</Tab>
          <Tab>Quality</Tab>
          <Tab>Hotspots</Tab>
        </TabList>
        <TabPanels>
          <TabPanel>
            <NodeTable rows={matches} />
          </TabPanel>
          <TabPanel>
            <Table size="lg" useZebraStyles>
              <TableHead>
                <TableRow>
                  <TableHeader>Community</TableHeader>
                  <TableHeader>Files</TableHeader>
                  <TableHeader>Members</TableHeader>
                </TableRow>
              </TableHead>
              <TableBody>
                {(graph?.communities ?? []).map((community) => (
                  <TableRow key={community.id}>
                    <TableCell>{community.id}</TableCell>
                    <TableCell>{community.size}</TableCell>
                    <TableCell>{community.files.join(", ")}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TabPanel>
          <TabPanel>
            <div className="stack-gap">
              <Table size="lg" useZebraStyles>
                <TableHead>
                  <TableRow>
                    <TableHeader>Hub</TableHeader>
                    <TableHeader>Degree</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {(graph?.hubs ?? []).map((hub) => (
                    <TableRow key={hub.id}>
                      <TableCell>{hub.id}</TableCell>
                      <TableCell>{hub.degree}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Table size="lg" useZebraStyles>
                <TableHead>
                  <TableRow>
                    <TableHeader>Bridge</TableHeader>
                    <TableHeader>Connects</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {(graph?.bridges ?? []).map((bridge) => (
                    <TableRow key={bridge.id}>
                      <TableCell>{bridge.id}</TableCell>
                      <TableCell>{bridge.connects.join(", ")}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </TabPanel>
          <TabPanel>
            <NodeTable rows={impact.map((id) => graph?.nodes.find((node) => node.id === id)).filter((node) => node != null)} />
          </TabPanel>
          <TabPanel>
            <Table size="lg" useZebraStyles>
              <TableHead>
                <TableRow>
                  <TableHeader>Signal</TableHeader>
                  <TableHeader>File</TableHeader>
                  <TableHeader>Lines</TableHeader>
                </TableRow>
              </TableHead>
              <TableBody>
                {(graph?.quality.large_files ?? []).map((file) => (
                  <TableRow key={file.id}>
                    <TableCell>Large file</TableCell>
                    <TableCell>{file.id}</TableCell>
                    <TableCell>{file.lines}</TableCell>
                  </TableRow>
                ))}
                {(graph?.quality.isolated_files ?? []).map((file) => (
                  <TableRow key={file}>
                    <TableCell>Isolated</TableCell>
                    <TableCell>{file}</TableCell>
                    <TableCell>—</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TabPanel>
          <TabPanel>
            <div className="stack-gap">
              {cochange?.message ? (
                <InlineNotification kind="warning" title="History" subtitle={cochange.message} lowContrast hideCloseButton />
              ) : (
                <p className="stat-label">
                {cochange?.commit_count ?? 0} commits, {cochange?.author_count ?? 0}{" "}
                {(cochange?.author_count ?? 0) === 1 ? "author" : "authors"}, window {cochange?.window_label || "—"}.
                Hidden coupling is a co-change pair with no import edge.
                </p>
              )}
              <Table size="lg" useZebraStyles>
                <TableHead>
                  <TableRow>
                    <TableHeader>File</TableHeader>
                    <TableHeader>Commits</TableHeader>
                    <TableHeader>Added</TableHeader>
                    <TableHeader>Deleted</TableHeader>
                    <TableHeader>Churn</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {(cochange?.hotspots ?? []).map((row) => (
                    <TableRow key={row.file}>
                      <TableCell>{row.file}</TableCell>
                      <TableCell>{row.commits}</TableCell>
                      <TableCell>{row.additions}</TableCell>
                      <TableCell>{row.deletions}</TableCell>
                      <TableCell>{row.churn_score}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <Table size="lg" useZebraStyles>
                <TableHead>
                  <TableRow>
                    <TableHeader>File A</TableHeader>
                    <TableHeader>File B</TableHeader>
                    <TableHeader>Coupling</TableHeader>
                    <TableHeader>Link</TableHeader>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {(cochange?.cochange ?? []).map((row) => (
                    <TableRow key={`${row.file_a}-${row.file_b}`}>
                      <TableCell>{row.file_a}</TableCell>
                      <TableCell>{row.file_b}</TableCell>
                      <TableCell>{row.coupling}</TableCell>
                      <TableCell>
                        <Tag type={row.is_structural ? "green" : "magenta"} size="sm">
                          {row.is_structural ? "import" : "hidden"}
                        </Tag>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </TabPanel>
        </TabPanels>
      </Tabs>
    </div>
  );
}

function NodeTable({ rows }: { rows: { id: string; language: string; lines: number }[] }) {
  return (
    <Table size="lg" useZebraStyles>
      <TableHead>
        <TableRow>
          <TableHeader>File</TableHeader>
          <TableHeader>Language</TableHeader>
          <TableHeader>Lines</TableHeader>
        </TableRow>
      </TableHead>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={row.id}>
            <TableCell>{row.id}</TableCell>
            <TableCell>{row.language}</TableCell>
            <TableCell>{row.lines}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function blast(graph: ImportGraph, seed: string): string[] {
  const start = graph.nodes.find((node) => node.id === seed || node.id.endsWith(seed));
  if (!start) return [];
  const outgoing = new Map<string, string[]>();
  for (const edge of graph.edges) {
    outgoing.set(edge.from, [...(outgoing.get(edge.from) ?? []), edge.to]);
    outgoing.set(edge.to, [...(outgoing.get(edge.to) ?? []), edge.from]);
  }
  const seen = new Set<string>([start.id]);
  const queue = [start.id];
  while (queue.length) {
    const current = queue.shift() as string;
    const nexts = outgoing.get(current) ?? [];
    for (const next of nexts) {
      if (!seen.has(next)) {
        seen.add(next);
        queue.push(next);
      }
    }
  }
  return [...seen];
}
