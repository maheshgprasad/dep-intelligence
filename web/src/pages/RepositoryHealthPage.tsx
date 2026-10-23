import { Button, InlineNotification, Tab, TabList, TabPanel, TabPanels, Tabs } from "@carbon/react";
import type { Snapshot } from "../api";
import { NamiDataTable } from "../components/NamiDataTable";
import { NamiEmptyState } from "../components/NamiEmptyState";
import { PageHeader } from "../components/PageHeader";
import { StatusTag } from "../components/StatusTag";

export function RepositoryHealthPage({ snapshot, loaded }: { snapshot: Snapshot | null; loaded: boolean }) {
  if (!loaded) return <PageHeader title="Repository health" description="Packages, security advisories, and test coverage." />;
  const matrix = snapshot?.matrix;
  const updates = snapshot?.updates;
  const vulns = snapshot?.vulnerabilities;
  const coverage = snapshot?.coverage;
  return (
    <div className="stack-gap">
      <PageHeader title="Repository health" description="Packages, dependency advisories, and generic test coverage. Change-specific checks stay on Review change." />
      <Tabs>
        <TabList aria-label="Repository health">
          <Tab>Packages</Tab>
          <Tab>Security</Tab>
          <Tab>Tests</Tab>
        </TabList>
        <TabPanels>
          <TabPanel>
            {!matrix ? (
              <NamiEmptyState title="No package scan" description="Package inventory is optional and is not part of Refresh intelligence." />
            ) : (
              <>
                <h2 className="section-title">Version mismatches</h2>
                <NamiDataTable
                  columns={[
                    { key: "name", header: "Package" },
                    { key: "ecosystem", header: "Ecosystem" },
                    { key: "versions", header: "Versions" },
                  ]}
                  rows={matrix.version_mismatches.map((item) => ({
                    id: `${item.ecosystem}-${item.name}`,
                    name: item.name,
                    ecosystem: item.ecosystem,
                    versions: Object.entries(item.versions).map(([repo, version]) => `${repo} ${version}`).join(", "),
                  }))}
                  emptyTitle="No version mismatches"
                  emptyDescription="Shared packages use the same version."
                />
                <h2 className="section-title">Available updates</h2>
                <NamiDataTable
                  columns={[
                    { key: "package", header: "Package" },
                    { key: "repo", header: "Repository" },
                    { key: "from", header: "From" },
                    { key: "to", header: "To" },
                    { key: "kind", header: "Update" },
                  ]}
                  rows={["major", "minor", "patch"].flatMap((kind) =>
                    (updates?.updates[kind as "major"] ?? []).map((item) => ({
                      id: `${kind}-${item.repo}-${item.package}`,
                      package: item.package,
                      repo: item.repo,
                      from: item.from,
                      to: item.to,
                      kind,
                    })),
                  )}
                  emptyTitle="No updates recorded"
                  emptyDescription="Update lookup has not been run, or every package is current."
                />
                <h2 className="section-title">Unique dependencies</h2>
                <NamiDataTable
                  searchable={matrix.unique_packages.length > 8}
                  columns={[
                    { key: "name", header: "Package" },
                    { key: "version", header: "Version" },
                    { key: "repo", header: "Repository" },
                  ]}
                  rows={matrix.unique_packages.map((item) => ({ id: `${item.repo}-${item.name}`, ...item }))}
                  emptyTitle="No unique packages"
                  emptyDescription="Every detected package is shared."
                />
              </>
            )}
          </TabPanel>
          <TabPanel>
            <h2 className="section-title">Dependency advisories</h2>
            <NamiDataTable
              columns={[
                { key: "severity", header: "Severity", render: (row) => <StatusTag status={row.severity || "info"} /> },
                { key: "package", header: "Package" },
                { key: "repo", header: "Repository" },
                { key: "summary", header: "Advisory" },
              ]}
              rows={(vulns?.findings ?? []).map((item, index) => ({ ...item, id: `${item.id}-${index}` }))}
              emptyTitle="No advisories"
              emptyDescription="OSV did not report a finding for the scanned packages."
              renderExpanded={(row) => (
                <p>{row.suggestion || "No upgrade suggestion was recorded."} {row.fixed ? `Fixed in ${row.fixed}.` : ""}</p>
              )}
            />
            <h2 className="section-title">Mend GitHub issues</h2>
            {(snapshot?.cves?.issues ?? []).length === 0 ? (
              <p className="helper">No Mend-style GitHub issues were scanned. This is optional and is not a universal security result.</p>
            ) : (
              <NamiDataTable
                columns={[
                  { key: "cve", header: "CVE" },
                  { key: "title", header: "Issue" },
                  { key: "repo", header: "Repository" },
                  { key: "state", header: "State" },
                ]}
                rows={(snapshot?.cves?.issues ?? []).map((item, index) => ({ id: `${item.url}-${index}`, ...item }))}
                emptyTitle="No Mend issues"
                emptyDescription="The optional Mend GitHub issue scan returned nothing."
              />
            )}
            {snapshot?.security_markdown ? (
              <Button
                kind="tertiary"
                onClick={() => download("security-report.md", snapshot.security_markdown)}
              >
                Download security report
              </Button>
            ) : null}
          </TabPanel>
          <TabPanel>
            {!coverage ? (
              <NamiEmptyState title="Coverage has not been scanned" description="Generic coverage is separate from review verification." />
            ) : (
              Object.entries(coverage.repositories).map(([name, repo]) => (
                <div key={name}>
                  <h2 className="section-title">{name}</h2>
                  {repo.status === "ok" || repo.percent != null ? (
                    <p>{repo.language} coverage {repo.percent == null ? "—" : `${Math.round(repo.percent)}%`}</p>
                  ) : (
                    <InlineNotification kind="info" title={`${repo.language} coverage ${repo.status || "not configured"}`} subtitle={repo.message || "Coverage is not configured for this language."} lowContrast hideCloseButton />
                  )}
                </div>
              ))
            )}
          </TabPanel>
        </TabPanels>
      </Tabs>
    </div>
  );
}

function download(name: string, contents: string) {
  const blob = new Blob([contents], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}
