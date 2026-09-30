import {
  Column,
  Grid,
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
  Tile,
} from "@carbon/react";
import type { Finding, Snapshot, Update } from "../api";

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
  const rows = Object.entries(snapshot?.languages?.repositories ?? {});
  return (
    <Table size="lg" useZebraStyles>
      <TableHead>
        <TableRow>
          <TableHeader>Repository</TableHeader>
          <TableHeader>Language</TableHeader>
          <TableHeader>Files read</TableHeader>
          <TableHeader>Confidence</TableHeader>
        </TableRow>
      </TableHead>
      <TableBody>
        {rows.map(([name, info]) => (
          <TableRow key={name}>
            <TableCell>{name}</TableCell>
            <TableCell>
              <Tag type={LANGUAGE_TAG[info.language_key] ?? "gray"} size="sm">
                {info.primary_language}
              </Tag>
            </TableCell>
            <TableCell>{info.total_files}</TableCell>
            <TableCell>{info.confidence}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function DependencyTable({ snapshot }: { snapshot: Snapshot | null }) {
  const mismatches = snapshot?.matrix?.version_mismatches ?? [];
  const repos = Object.keys(snapshot?.matrix?.repositories ?? {});
  const unique = snapshot?.matrix?.unique_packages ?? [];
  return (
    <div className="stack-gap">
      <Table size="lg" useZebraStyles>
        <TableHead>
          <TableRow>
            <TableHeader>Package</TableHeader>
            {repos.map((repo) => (
              <TableHeader key={repo}>{repo}</TableHeader>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {mismatches.map((item) => (
            <TableRow key={`${item.ecosystem}-${item.name}`}>
              <TableCell>
                {item.name} <Tag size="sm">{item.ecosystem}</Tag>
              </TableCell>
              {repos.map((repo) => (
                <TableCell key={repo}>
                  {item.versions[repo] ? (
                    <Tag type="red" size="sm">
                      {item.versions[repo]}
                    </Tag>
                  ) : (
                    "—"
                  )}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <p className="stat-label">
        {(snapshot?.matrix?.common_packages.length ?? 0)} shared packages, {unique.length} unique.
        The table above lists version mismatches.
      </p>
      {repos.map((repo) => {
        const rows = unique.filter((item) => item.repo === repo);
        return (
          <section key={repo} className="stack-gap">
            <h2 className="section-title">{repo}</h2>
            <p className="stat-label">{rows.length} unique {rows.length === 1 ? "package" : "packages"}</p>
            <Table size="lg" useZebraStyles>
              <TableHead>
                <TableRow>
                  <TableHeader>Package</TableHeader>
                  <TableHeader>Ecosystem</TableHeader>
                  <TableHeader>Version</TableHeader>
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.length ? (
                  rows.map((item) => (
                    <TableRow key={`${item.ecosystem}-${item.name}`}>
                      <TableCell>{item.name}</TableCell>
                      <TableCell>
                        <Tag size="sm">{item.ecosystem}</Tag>
                      </TableCell>
                      <TableCell>{item.version || "—"}</TableCell>
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell>None</TableCell>
                    <TableCell>—</TableCell>
                    <TableCell>—</TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </section>
        );
      })}
    </div>
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
    <Table size="lg" useZebraStyles>
      <TableHead>
        <TableRow>
          <TableHeader>Level</TableHeader>
          <TableHeader>Package</TableHeader>
          <TableHeader>Repository</TableHeader>
          <TableHeader>From</TableHeader>
          <TableHeader>To</TableHeader>
        </TableRow>
      </TableHead>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={`${row.repo}-${row.package}-${row.kind}`}>
            <TableCell>
              <Tag type={row.type} size="sm">
                {row.kind}
              </Tag>
            </TableCell>
            <TableCell>{row.package}</TableCell>
            <TableCell>{row.repo}</TableCell>
            <TableCell>{row.from}</TableCell>
            <TableCell>{row.to}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function ApiTable({ snapshot }: { snapshot: Snapshot | null }) {
  const dependencies = snapshot?.apis?.api_dependencies.dependencies ?? [];
  return (
    <Table size="lg" useZebraStyles>
      <TableHead>
        <TableRow>
          <TableHeader>From</TableHeader>
          <TableHeader>To</TableHeader>
          <TableHeader>Method</TableHeader>
          <TableHeader>Endpoint</TableHeader>
          <TableHeader>Confidence</TableHeader>
        </TableRow>
      </TableHead>
      <TableBody>
        {dependencies.map((edge) => (
          <TableRow key={`${edge.from}-${edge.to}-${edge.endpoint}`}>
            <TableCell>{edge.from}</TableCell>
            <TableCell>{edge.to}</TableCell>
            <TableCell>{edge.method}</TableCell>
            <TableCell>{edge.endpoint}</TableCell>
            <TableCell>
              <Tag type="green" size="sm">
                {edge.confidence}
              </Tag>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function ReviewTable({ snapshot }: { snapshot: Snapshot | null }) {
  const rows: (Finding & { kind: string })[] = [
    ...(snapshot?.review?.findings.dead_code ?? []).map((item) => ({ ...item, kind: "Dead code" })),
    ...(snapshot?.review?.findings.undefined_variables ?? []).map((item) => ({ ...item, kind: "Undefined" })),
  ];
  return (
    <Table size="lg" useZebraStyles>
      <TableHead>
        <TableRow>
          <TableHeader>Kind</TableHeader>
          <TableHeader>Repository</TableHeader>
          <TableHeader>File</TableHeader>
          <TableHeader>Line</TableHeader>
          <TableHeader>Detail</TableHeader>
        </TableRow>
      </TableHead>
      <TableBody>
        {rows.map((row) => (
          <TableRow key={`${row.kind}-${row.repo}-${row.file}-${row.line}-${row.name}`}>
            <TableCell>
              <Tag type={row.kind === "Dead code" ? "gray" : "red"} size="sm">
                {row.kind}
              </Tag>
            </TableCell>
            <TableCell>{row.repo}</TableCell>
            <TableCell>{row.file}</TableCell>
            <TableCell>{row.line}</TableCell>
            <TableCell>{row.message}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function CoverageTable({ snapshot }: { snapshot: Snapshot | null }) {
  const rows = Object.entries(snapshot?.coverage?.repositories ?? {});
  return (
    <Table size="lg" useZebraStyles>
      <TableHead>
        <TableRow>
          <TableHeader>Repository</TableHeader>
          <TableHeader>Language</TableHeader>
          <TableHeader>Status</TableHeader>
          <TableHeader>Coverage</TableHeader>
          <TableHeader>Detail</TableHeader>
        </TableRow>
      </TableHead>
      <TableBody>
        {rows.map(([name, info]) => (
          <TableRow key={name}>
            <TableCell>{name}</TableCell>
            <TableCell>{info.language}</TableCell>
            <TableCell>
              <Tag type={info.status === "real" ? "green" : "gray"} size="sm">
                {info.status}
              </Tag>
            </TableCell>
            <TableCell>{info.percent == null ? "—" : `${Math.round(info.percent)}%`}</TableCell>
            <TableCell>{info.message}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
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
      <Table size="lg" useZebraStyles>
        <TableHead>
          <TableRow>
            <TableHeader>Repository</TableHeader>
            <TableHeader>Package</TableHeader>
            <TableHeader>Advisory</TableHeader>
            <TableHeader>Suggestion</TableHeader>
          </TableRow>
        </TableHead>
        <TableBody>
          {suggestions.map((item) => (
            <TableRow key={`${item.repo}-${item.id}-${item.package}-${item.version}`}>
              <TableCell>{item.repo}</TableCell>
              <TableCell>
                {item.package}@{item.version}
              </TableCell>
              <TableCell>{item.id || "—"}</TableCell>
              <TableCell>{item.suggestion}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {snapshot?.security_markdown ? <pre className="report">{snapshot.security_markdown}</pre> : null}
    </div>
  );
}
