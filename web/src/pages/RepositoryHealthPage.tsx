import { useEffect, useState } from "react";
import { Button, InlineLoading, InlineNotification, Tab, TabList, TabPanel, TabPanels, Tabs } from "@carbon/react";
import { downloadSecurityReport, healthPackages, healthSecurity, healthTests, refreshHealth, reviewJob } from "../api";
import { NamiDataTable } from "../components/NamiDataTable";
import { NamiEmptyState } from "../components/NamiEmptyState";
import { PageHeader } from "../components/PageHeader";
import { StatusTag } from "../components/StatusTag";

export function RepositoryHealthPage({ loaded }: { loaded: boolean }) {
  const [section, setSection] = useState("packages");
  const [packages, setPackages] = useState<Awaited<ReturnType<typeof healthPackages>> | null>(null);
  const [security, setSecurity] = useState<Awaited<ReturnType<typeof healthSecurity>> | null>(null);
  const [tests, setTests] = useState<Awaited<ReturnType<typeof healthTests>> | null>(null);
  const [busy, setBusy] = useState("");
  const [note, setNote] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    if (section === "packages") {
      void healthPackages(controller.signal).then(setPackages).catch(() => undefined);
    } else if (section === "security") {
      void healthSecurity(controller.signal).then(setSecurity).catch(() => undefined);
    } else {
      void healthTests(controller.signal).then(setTests).catch(() => undefined);
    }
    return () => controller.abort();
  }, [section, busy]);

  async function scan() {
    setNote("");
    const started = await refreshHealth();
    setBusy(started.job_id);
    const timer = window.setInterval(() => {
      void reviewJob(started.job_id).then((job) => {
        if (job.status === "succeeded" || job.status === "failed" || job.status === "cancelled") {
          window.clearInterval(timer);
          setBusy("");
          setNote(job.status === "succeeded" ? "Repository health scan finished." : job.error || "The health scan did not finish.");
        }
      });
    }, 3000);
  }

  if (!loaded) return <PageHeader title="Repository health" description="Packages, security advisories, and test coverage." />;
  const matrix = packages?.matrix;
  const updates = packages?.updates;
  const coverage = tests?.coverage;
  return (
    <div className="stack-gap">
      <PageHeader
        title="Repository health"
        description="Packages, dependency advisories, and generic test coverage. Change-specific checks stay on Review change."
        actions={busy ? <InlineLoading description="Scanning repository health" /> : <Button kind="primary" onClick={() => void scan()}>Run repository health scan</Button>}
      />
      {note ? <InlineNotification kind="info" title="Repository health" subtitle={note} lowContrast hideCloseButton /> : null}
      <Tabs onChange={({ selectedIndex }) => setSection(["packages", "security", "tests"][selectedIndex] || "packages")}>
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
              </>
            )}
          </TabPanel>
          <TabPanel>
            <h2 className="section-title">Security advisories</h2>
            <NamiDataTable
              columns={[
                { key: "severity", header: "Severity", render: (row) => <StatusTag status={row.severity || "info"} /> },
                { key: "package", header: "Package" },
                { key: "repo", header: "Repository" },
                { key: "summary", header: "Advisory" },
              ]}
              rows={(security?.advisories ?? []).map((item, index) => ({ ...item, id: `${item.id}-${index}` }))}
              emptyTitle="No advisories"
              emptyDescription="OSV did not report a finding, or a security scan has not been run."
            />
            <h2 className="section-title">Dependency hygiene</h2>
            <NamiDataTable
              columns={[
                { key: "package", header: "Package" },
                { key: "repo", header: "Repository" },
                { key: "summary", header: "Note" },
              ]}
              rows={(security?.hygiene ?? []).map((item, index) => ({ ...item, id: `hygiene-${index}`, summary: item.suggestion || item.summary }))}
              emptyTitle="No hygiene notes"
              emptyDescription="Version and pinning notes appear here. They are not vulnerabilities."
            />
            <h2 className="section-title">Mend GitHub issues</h2>
            <p className="helper">Optional vendor output. It is not a universal security result.</p>
            <NamiDataTable
              columns={[
                { key: "cve", header: "CVE" },
                { key: "title", header: "Issue" },
                { key: "repo", header: "Repository" },
                { key: "state", header: "State" },
              ]}
              rows={(security?.cves?.issues ?? []).map((item, index) => ({ id: `${item.url}-${index}`, ...item }))}
              emptyTitle="No Mend issues"
              emptyDescription="The optional Mend GitHub issue scan has not been run or returned nothing."
            />
            <Button kind="tertiary" onClick={() => void downloadSecurityReport().catch(() => setNote("No security report is stored."))}>Download security report</Button>
          </TabPanel>
          <TabPanel>
            {!coverage ? (
              <NamiEmptyState title="Coverage has not been scanned" description="Generic coverage is separate from review verification." />
            ) : (
              <NamiDataTable
                columns={[
                  { key: "repository", header: "Repository" },
                  { key: "language", header: "Language" },
                  { key: "coverage", header: "Coverage" },
                  { key: "status", header: "Status" },
                ]}
                rows={Object.entries(coverage.repositories).map(([name, repo]) => ({
                  id: name,
                  repository: name,
                  language: repo.language,
                  coverage: repo.percent == null ? "—" : `${Math.round(repo.percent)}%`,
                  status: repo.percent == null ? "Not configured" : "Available",
                  message: repo.message,
                }))}
                emptyTitle="No coverage results"
                emptyDescription="Run a repository health scan to collect coverage."
                renderExpanded={(row) => <p>{row.message || "No extra coverage detail was recorded."}</p>}
              />
            )}
          </TabPanel>
        </TabPanels>
      </Tabs>
    </div>
  );
}
