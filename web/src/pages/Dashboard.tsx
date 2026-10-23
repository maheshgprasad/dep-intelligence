import { useMemo, useState } from "react";
import {
  Column,
  Dropdown,
  Grid,
  InlineNotification,
  Tab,
  TabList,
  TabPanel,
  TabPanels,
  Tabs,
  Tag,
  Tile,
} from "@carbon/react";
import type { Finding, Snapshot, Update } from "../api";
import { CarbonTable, type CarbonRow } from "../components/CarbonTable";

const LANGUAGE_TAG: Record<string, "blue" | "cyan" | "teal" | "purple" | "green" | "magenta" | "gray"> = {
  python: "blue",
  javascript: "teal",
  typescript: "purple",
  go: "cyan",
  java: "magenta",
};

export function Dashboard({ snapshot, error, loaded }: { snapshot: Snapshot | null; error: string; loaded: boolean }) {
  const repos = snapshot?.matrix?.meta.total_repos ?? Object.keys(snapshot?.languages?.repositories ?? {}).length;
  const packages = snapshot?.matrix?.meta.total_packages ?? 0;
  const updates = snapshot?.updates?.summary.total ?? 0;
  const findings = snapshot?.review?.summary.total ?? 0;
  const hasData = Boolean(snapshot?.matrix || snapshot?.languages);

  return (
    <div className="stack-gap">
      <div className="page-head">
        <div>
          <h1>Dependency intelligence</h1>
          <p>Cross-repo packages, APIs, coverage, and advisories.</p>
        </div>
      </div>
      {error ? <InlineNotification kind="error" title="API" subtitle={error} lowContrast hideCloseButton /> : null}
      {snapshot?.analysis_status && snapshot.analysis_status.status && snapshot.analysis_status.status !== "success" ? (
        <InlineNotification
          kind={snapshot.analysis_status.status === "failed" ? "error" : "warning"}
          title={`Analysis ${snapshot.analysis_status.status}`}
          subtitle={snapshot.analysis_status.message || "One or more steps failed. Successful outputs are still available."}
          lowContrast
          hideCloseButton
        />
      ) : null}
      {!error && loaded && !hasData ? (
        <InlineNotification
          kind="info"
          title="No scan yet"
          subtitle="Run analysis to read the repositories in repos.txt."
          lowContrast
          hideCloseButton
        />
      ) : null}
      <Grid condensed>
        <Column lg={4} md={2} sm={4}>
          <Tile>
            <p className="stat-label">Repositories</p>
            <p className="stat-value">{repos}</p>
          </Tile>
        </Column>
        <Column lg={4} md={2} sm={4}>
          <Tile>
            <p className="stat-label">Packages</p>
            <p className="stat-value">{packages}</p>
          </Tile>
        </Column>
        <Column lg={4} md={2} sm={4}>
          <Tile>
            <p className="stat-label">Updates</p>
            <p className="stat-value">{updates}</p>
          </Tile>
        </Column>
        <Column lg={4} md={2} sm={4}>
          <Tile>
            <p className="stat-label">Review findings</p>
            <p className="stat-value">{findings}</p>
          </Tile>
        </Column>
      </Grid>
      <Tabs>
        <TabList aria-label="Analysis views" contained>
          <Tab>Repositories</Tab>
          <Tab>Dependencies</Tab>
          <Tab>Updates</Tab>
          <Tab>APIs</Tab>
          <Tab>Review</Tab>
          <Tab>Coverage</Tab>
          <Tab>Security</Tab>
        </TabList>
        <TabPanels>
          <TabPanel>
            <RepoTable snapshot={snapshot} />
          </TabPanel>
          <TabPanel>
            <DependencyTable snapshot={snapshot} />
          </TabPanel>
          <TabPanel>
            <UpdateTable snapshot={snapshot} />
          </TabPanel>
          <TabPanel>
            <ApiTable snapshot={snapshot} />
          </TabPanel>
          <TabPanel>
            <ReviewTable snapshot={snapshot} />
          </TabPanel>
          <TabPanel>
            <CoverageTable snapshot={snapshot} />
          </TabPanel>
          <TabPanel>
            <SecurityPanel snapshot={snapshot} />
          </TabPanel>
        </TabPanels>
      </Tabs>
    </div>
  );
}

function RepoTable({ snapshot }: { snapshot: Snapshot | null }) {
  const rows = Object.entries(snapshot?.languages?.repositories ?? {}).map(([name, info]) => ({
    id: name,
    repository: name,
    language: info.primary_language,
    language_key: info.language_key,
    files: info.total_files,
    confidence: info.confidence,
  }));
  return (
    <CarbonTable
      title="Repositories"
      description="Languages detected for the repositories in repos.txt."
      headers={[
        { key: "repository", header: "Repository" },
        { key: "language", header: "Language" },
        { key: "files", header: "Files read" },
        { key: "confidence", header: "Confidence" },
      ]}
      rows={rows}
      searchPlaceholder="Search repositories"
      empty="No repositories have been scanned."
      renderCell={(key, value, row) =>
        key === "language" ? (
          <Tag type={LANGUAGE_TAG[String(row.language_key)] ?? "gray"} size="sm">
            {value}
          </Tag>
        ) : (
          value
        )
      }
    />
  );
}

function DependencyTable({ snapshot }: { snapshot: Snapshot | null }) {
  const mismatches = snapshot?.matrix?.version_mismatches ?? [];
  const repos = Object.keys(snapshot?.matrix?.repositories ?? {});
  const unique = snapshot?.matrix?.unique_packages ?? [];
  const headers = [
    { key: "package", header: "Package" },
    { key: "ecosystem", header: "Ecosystem" },
    ...repos.map((repo, index) => ({ key: `v${index}`, header: repo })),
  ];
  const mismatchRows: CarbonRow[] = mismatches.map((item) => {
    const row: CarbonRow = {
      id: `${item.ecosystem}-${item.name}`,
      package: item.name,
      ecosystem: item.ecosystem,
    };
    repos.forEach((repo, index) => {
      row[`v${index}`] = item.versions[repo] || "—";
    });
    return row;
  });
  return (
    <div className="stack-gap">
      <CarbonTable
        title="Version mismatches"
        description={`${snapshot?.matrix?.common_packages.length ?? 0} shared packages, ${unique.length} unique.`}
        headers={headers}
        rows={mismatchRows}
        searchPlaceholder="Search packages"
        empty="No version mismatches."
        renderCell={(key, value) => {
          if (key === "ecosystem") return <Tag size="sm">{value}</Tag>;
          if (key.startsWith("v") && value !== "—") return <Tag type="red" size="sm">{value}</Tag>;
          return value;
        }}
      />
      <UniquePackageTable rows={unique} repositories={repos} />
    </div>
  );
}

const PACKAGE_HEADERS = [
  { key: "package", header: "Package" },
  { key: "ecosystem", header: "Ecosystem" },
  { key: "version", header: "Version" },
  { key: "repository", header: "Repository" },
];

function UniquePackageTable({
  rows,
  repositories,
}: {
  rows: { name: string; ecosystem: string; repo: string; version: string }[];
  repositories: string[];
}) {
  const [repository, setRepository] = useState("*");
  const repoItems = useMemo(() => ["*", ...repositories], [repositories]);
  const filtered = rows.filter((item) => repository === "*" || item.repo === repository);
  return (
    <CarbonTable
      title="Unique packages"
      description="Packages that appear in only one repository. Choose a repository or search to narrow the list."
      headers={PACKAGE_HEADERS}
      rows={filtered.map((item) => ({
        id: `${item.repo}-${item.ecosystem}-${item.name}`,
        package: item.name,
        ecosystem: item.ecosystem,
        version: item.version || "—",
        repository: item.repo,
      }))}
      searchPlaceholder="Search packages"
      empty="No unique packages match this repository or search."
      toolbar={
        <div className="carbon-table-filter">
          <Dropdown
            id="unique-package-repo"
            titleText="Repository"
            label="All repositories"
            items={repoItems}
            itemToString={(item) => (item === "*" ? "All repositories" : item || "")}
            selectedItem={repository}
            onChange={({ selectedItem }) => setRepository(selectedItem || "*")}
          />
        </div>
      }
      renderCell={(key, value) => (key === "ecosystem" ? <Tag size="sm">{value}</Tag> : value)}
    />
  );
}

function UpdateTable({ snapshot }: { snapshot: Snapshot | null }) {
  const groups: { label: string; type: "red" | "magenta" | "cyan"; rows: Update[] }[] = [
    { label: "Major", type: "red", rows: snapshot?.updates?.updates.major ?? [] },
    { label: "Minor", type: "magenta", rows: snapshot?.updates?.updates.minor ?? [] },
    { label: "Patch", type: "cyan", rows: snapshot?.updates?.updates.patch ?? [] },
  ];
  const rows = groups.flatMap((group) => group.rows.map((row) => ({ ...row, kind: group.label, type: group.type })));
  return (
    <CarbonTable
      title="Package updates"
      description="Major, minor, and patch updates found for packages in repos.txt."
      headers={[
        { key: "level", header: "Level" },
        { key: "package", header: "Package" },
        { key: "repository", header: "Repository" },
        { key: "from", header: "From" },
        { key: "to", header: "To" },
      ]}
      rows={rows.map((row) => ({
        id: `${row.repo}-${row.package}-${row.kind}`,
        level: row.kind,
        package: row.package,
        repository: row.repo,
        from: row.from,
        to: row.to,
      }))}
      searchPlaceholder="Search updates"
      empty="No package updates."
      renderCell={(key, value) =>
        key === "level" ? (
          <Tag type={value === "Major" ? "red" : value === "Minor" ? "magenta" : "cyan"} size="sm">
            {value}
          </Tag>
        ) : (
          value
        )
      }
    />
  );
}

function ApiTable({ snapshot }: { snapshot: Snapshot | null }) {
  const bundle = snapshot?.apis?.api_dependencies;
  const services = bundle?.services ?? {};
  const dependencies = bundle?.dependencies ?? [];
  const diagnostics = bundle?.diagnostics ?? [];
  const endpoints = Object.entries(services).flatMap(([service, info]) =>
    (info.exposes ?? []).map((route) => ({ service, ...route })),
  );
  const calls = Object.entries(services).flatMap(([service, info]) =>
    (info.consumes ?? []).map((call) => ({ service, ...call })),
  );
  return (
    <>
      <InlineNotification
        kind="info"
        lowContrast
        hideCloseButton
        title="How this tab is filled"
        subtitle="Analysis shallow-clones each GitHub repository, reads routes and HTTP clients from that tree, saves this report, then deletes the checkout."
      />
      {!snapshot?.apis ? (
        <InlineNotification kind="warning" lowContrast hideCloseButton title="No API report yet" subtitle="Run analysis to scan the repositories in repos.txt." />
      ) : null}
      {diagnostics.map((item) => (
        <InlineNotification
          key={`${item.code}-${item.service ?? ""}-${item.message}`}
          kind="warning"
          lowContrast
          hideCloseButton
          title={item.service || item.code}
          subtitle={item.message}
        />
      ))}
      <CarbonTable
        title="Routes and clients"
        description="HTTP routes this repository serves, and HTTP calls it makes."
        headers={[
          { key: "repository", header: "Repository" },
          { key: "role", header: "Role" },
          { key: "method", header: "Method" },
          { key: "path", header: "Path" },
          { key: "file", header: "File" },
        ]}
        rows={[
          ...endpoints.map((route) => ({
            id: `serve-${route.service}-${route.method}-${route.path}-${route.file ?? ""}`,
            repository: route.service,
            role: "Serves",
            method: route.method,
            path: route.path,
            file: route.file || "—",
          })),
          ...calls.map((call) => ({
            id: `call-${call.service}-${call.method}-${call.url}-${call.file ?? ""}`,
            repository: call.service,
            role: "Calls",
            method: call.method,
            path: call.url,
            file: call.file || "—",
          })),
        ]}
        searchPlaceholder="Search routes"
        empty="No routes or HTTP clients were found in the scanned source."
      />
      <CarbonTable
        title="Cross-service calls"
        description="Calls matched to a route served by another repository."
        headers={[
          { key: "from", header: "From" },
          { key: "to", header: "To" },
          { key: "method", header: "Method" },
          { key: "endpoint", header: "Endpoint" },
          { key: "confidence", header: "Confidence" },
        ]}
        rows={dependencies.map((edge) => ({
          id: `${edge.from}-${edge.to}-${edge.endpoint}`,
          from: edge.from,
          to: edge.to,
          method: edge.method,
          endpoint: edge.endpoint,
          confidence: edge.confidence,
        }))}
        searchPlaceholder="Search calls"
        empty="No cross-service calls were matched."
        renderCell={(key, value) => (key === "confidence" ? <Tag type="green" size="sm">{value}</Tag> : value)}
      />
    </>
  );
}

function ReviewTable({ snapshot }: { snapshot: Snapshot | null }) {
  const rows: (Finding & { kind: string })[] = [
    ...(snapshot?.review?.findings.dead_code ?? []).map((item) => ({ ...item, kind: "Dead code" })),
    ...(snapshot?.review?.findings.undefined_variables ?? []).map((item) => ({ ...item, kind: "Undefined" })),
  ];
  return (
    <CarbonTable
      title="Review findings"
      description="Dead code and undefined names found in the scanned repositories."
      headers={[
        { key: "kind", header: "Kind" },
        { key: "repository", header: "Repository" },
        { key: "file", header: "File" },
        { key: "line", header: "Line" },
        { key: "detail", header: "Detail" },
      ]}
      rows={rows.map((row) => ({
        id: `${row.kind}-${row.repo}-${row.file}-${row.line}-${row.name}`,
        kind: row.kind,
        repository: row.repo,
        file: row.file,
        line: row.line,
        detail: row.message,
      }))}
      searchPlaceholder="Search findings"
      empty="No review findings."
      renderCell={(key, value) =>
        key === "kind" ? (
          <Tag type={value === "Dead code" ? "gray" : "red"} size="sm">
            {value}
          </Tag>
        ) : (
          value
        )
      }
    />
  );
}

function CoverageTable({ snapshot }: { snapshot: Snapshot | null }) {
  const rows = Object.entries(snapshot?.coverage?.repositories ?? {});
  return (
    <>
      <InlineNotification
        kind="info"
        lowContrast
        hideCloseButton
        title="How this tab is filled"
        subtitle="Python and Go tests run inside a shallow checkout of each GitHub repository. The checkout is deleted after this report is saved. JavaScript coverage is not run."
      />
      {!snapshot?.coverage ? (
        <InlineNotification kind="warning" lowContrast hideCloseButton title="No coverage report yet" subtitle="Run analysis to execute pytest or go test where those languages are present." />
      ) : null}
      <CarbonTable
        title="Coverage"
        description="Pytest or go test results from each repository checkout."
        headers={[
          { key: "repository", header: "Repository" },
          { key: "language", header: "Language" },
          { key: "status", header: "Status" },
          { key: "coverage", header: "Coverage" },
          { key: "detail", header: "Detail" },
        ]}
        rows={rows.map(([name, info]) => ({
          id: name,
          repository: name,
          language: info.language,
          status: info.status,
          coverage: info.percent == null ? "—" : `${Math.round(info.percent)}%`,
          detail: info.message,
        }))}
        searchPlaceholder="Search coverage"
        empty="The coverage report has no repositories."
        renderCell={(key, value) =>
          key === "status" ? (
            <Tag type={value === "real" ? "green" : "gray"} size="sm">
              {value}
            </Tag>
          ) : (
            value
          )
        }
      />
    </>
  );
}

function SecurityPanel({ snapshot }: { snapshot: Snapshot | null }) {
  const report = snapshot?.vulnerabilities;
  const scanned = report?.meta?.scanned ?? report?.scanned?.length ?? 0;
  const advisories = (report?.findings ?? []).filter((item) => item.id);
  const suggestions = (report?.findings ?? []).filter((item) => item.suggestion);
  return (
    <div className="stack-gap">
      <InlineNotification
        kind="info"
        title="OSV"
        subtitle={`${scanned} packages from repos.txt were queried one by one against https://osv.dev/. ${advisories.length} advisories, ${suggestions.length} suggestions.`}
        lowContrast
        hideCloseButton
      />
      <CarbonTable
        title="Advisories"
        description="Suggestions returned for the packages queried against OSV."
        headers={[
          { key: "repository", header: "Repository" },
          { key: "package", header: "Package" },
          { key: "advisory", header: "Advisory" },
          { key: "suggestion", header: "Suggestion" },
        ]}
        rows={suggestions.map((item) => ({
          id: `${item.repo}-${item.id}-${item.package}-${item.version}`,
          repository: item.repo,
          package: `${item.package}@${item.version}`,
          advisory: item.id || "—",
          suggestion: item.suggestion || "—",
        }))}
        searchPlaceholder="Search advisories"
        empty="No advisory suggestions."
      />
      {snapshot?.security_markdown ? <pre className="report">{snapshot.security_markdown}</pre> : null}
    </div>
  );
}
