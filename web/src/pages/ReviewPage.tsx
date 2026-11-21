import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  Button,
  ComboBox,
  Dropdown,
  Grid,
  Column,
  InlineLoading,
  InlineNotification,
  RadioButton,
  RadioButtonGroup,
  TextInput,
  Toggle,
} from "@carbon/react";
import { getServiceClusters, previewChange, purgeReviews, recentReviews, reviewConfig, reviewSourceOptions, runReview, type AppState, type ServiceClusterSummary } from "../api";
import { hideFilePaths } from "../labels";
import { reviewFormState, scopeColumnLabel } from "../reviewForm";
import { NamiDataTable } from "../components/NamiDataTable";
import { NamiEmptyState } from "../components/NamiEmptyState";
import { PageHeader } from "../components/PageHeader";
import { StatusTag } from "../components/StatusTag";

const REVIEW_SCOPES = [
  { id: "repository", text: "Individual repository" },
  { id: "service_cluster", text: "Service cluster" },
];

const SOURCES = [
  { id: "working_tree", text: "Working tree" },
  { id: "staged", text: "Staged changes" },
  { id: "commit", text: "Commit" },
  { id: "range", text: "Branch / commit range" },
  { id: "github_pr", text: "Pull request" },
];

const MODE_LABELS = { auto: "Recommended", fast: "Fast", exact: "Exact" } as const;

const SOURCE_LABELS: Record<string, string> = {
  working_tree: "Working tree",
  staged: "Staged changes",
  commit: "Commit",
  range: "Commit range",
  github_pr: "Pull request",
  explicit: "Selected files",
};

export function ReviewPage({ snapshot }: { snapshot: AppState | null }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const sourceId = params.get("source") || "working_tree";
  const source = SOURCES.find((item) => item.id === sourceId) ?? SOURCES[0];
  const repos = (snapshot?.repositories ?? []).filter((repo) => repo.checkout_status !== "unavailable" && repo.slug);
  const [reviewScope, setReviewScope] = useState<"repository" | "service_cluster">("repository");
  const [clusters, setClusters] = useState<ServiceClusterSummary[]>([]);
  const [clusterId, setClusterId] = useState("");
  const [repository, setRepository] = useState(params.get("repository") || repos[0]?.slug || "");
  const [commit, setCommit] = useState("");
  const [base, setBase] = useState("");
  const [head, setHead] = useState("");
  const [prUrl, setPrUrl] = useState("");
  const [mode, setMode] = useState<"auto" | "fast" | "exact">("auto");
  const [verify, setVerify] = useState(false);
  const [model, setModel] = useState(false);
  const [modelReady, setModelReady] = useState(false);
  const [verificationCommands, setVerificationCommands] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [options, setOptions] = useState<Awaited<ReturnType<typeof reviewSourceOptions>> | null>(null);
  const [preview, setPreview] = useState<{ files: number; symbols: number; contracts: number; alignment: string; notes: string[] } | null>(null);
  const [reviews, setReviews] = useState<Awaited<ReturnType<typeof recentReviews>>>([]);
  const [confirmPurge, setConfirmPurge] = useState(false);
  const [purgeNote, setPurgeNote] = useState("");
  const [purgeError, setPurgeError] = useState("");

  const selectedCluster = clusters.find((item) => item.cluster_id === clusterId) ?? null;
  const clusterRepos = selectedCluster ? repos.filter((repo) => selectedCluster.services.includes(serviceIdOf(repo))) : [];
  const visibleRepos = reviewScope === "service_cluster" ? clusterRepos : repos;
  const defaultRepository = (reviewScope === "service_cluster" ? clusterRepos[0]?.slug : repos[0]?.slug) || "";
  useEffect(() => {
    void getServiceClusters().then((payload) => setClusters(payload.clusters ?? [])).catch(() => setClusters([]));
  }, []);

  useEffect(() => {
    if (reviewScope !== "service_cluster" || clusters.length === 0) return;
    const cluster = clusters.find((item) => item.cluster_id === clusterId) ?? clusters[0];
    if (cluster.cluster_id !== clusterId) setClusterId(cluster.cluster_id);
    const members = repos.filter((repo) => cluster.services.includes(serviceIdOf(repo)));
    if (members.length > 0 && !members.some((repo) => repo.slug === repository)) setRepository(members[0].slug);
  }, [reviewScope, clusters, clusterId, repos, repository]);

  useEffect(() => {
    if (!repository && defaultRepository) setRepository(defaultRepository);
  }, [repository, defaultRepository]);

  useEffect(() => {
    void reviewConfig(repository).then((config) => {
      const commands = config.verification?.commands ?? [];
      setModelReady(config.model_configured);
      setVerificationCommands(commands);
      setVerify(commands.length > 0);
    }).catch(() => {
      setModelReady(false);
      setVerificationCommands([]);
      setVerify(false);
    });
  }, [repository]);

  useEffect(() => {
    if (!repository) return;
    const controller = new AbortController();
    void reviewSourceOptions(repository, controller.signal).then(setOptions).catch(() => setOptions(null));
    return () => controller.abort();
  }, [repository]);

  useEffect(() => {
    void recentReviews().then(setReviews).catch(() => setReviews([]));
  }, []);

  function chooseScope(next: "repository" | "service_cluster") {
    setReviewScope(next);
    setPreview(null);
    if (next !== "service_cluster") return;
    const cluster = clusters.find((item) => item.cluster_id === clusterId) ?? clusters[0];
    if (!cluster) return;
    setClusterId(cluster.cluster_id);
    const members = repos.filter((repo) => cluster.services.includes(serviceIdOf(repo)));
    if (!members.some((repo) => repo.slug === repository) && members[0]) setRepository(members[0].slug);
  }

  function chooseCluster(nextId: string) {
    setClusterId(nextId);
    setPreview(null);
    const cluster = clusters.find((item) => item.cluster_id === nextId);
    if (!cluster) return;
    const members = repos.filter((repo) => cluster.services.includes(serviceIdOf(repo)));
    if (!members.some((repo) => repo.slug === repository) && members[0]) setRepository(members[0].slug);
  }

  const originRepo = repos.find((item) => item.slug === repository);
  const originLabel = reviewScope === "service_cluster" ? serviceIdOf(originRepo) || originRepo?.name || repository : originRepo?.name || repository;
  const form = reviewFormState({
    reviewScope,
    clusterId,
    repository,
    repositoryLabel: originLabel,
    source: source.id as "working_tree" | "staged" | "commit" | "range" | "github_pr",
    commit,
    base,
    head,
    prUrl,
    dirtyFiles: options ? options.dirty_files : null,
    stagedFiles: options ? options.staged_files : null,
    branch: options?.current_branch || "",
    verificationCommands,
    verifyRequested: verify,
    mode,
  });

  function sourceBody() {
    const clusterReview = reviewScope === "service_cluster";
    return {
      mode,
      verify: form.verify,
      model: modelReady && model ? { enabled: true } : {},
      review_scope: reviewScope,
      cluster_id: clusterReview ? clusterId : "",
      service: clusterReview ? serviceIdOf(originRepo) : "",
      source: {
        kind: source.id,
        repository: source.id === "github_pr" && !clusterReview ? "" : repository,
        commit: source.id === "commit" ? commit : null,
        base_ref: source.id === "range" ? base : null,
        head_ref: source.id === "range" ? head : null,
        pr_url: source.id === "github_pr" ? prUrl : null,
      },
    };
  }

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const report = await runReview(sourceBody());
      if (report.job_id) navigate(`/review/${report.job_id}?job=1`);
      else if (report.review_id) navigate(`/review/${report.review_id}`);
      else setError("The review finished without a stored result.");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "The review could not be started.");
    } finally {
      setBusy(false);
    }
  }

  async function previewSource() {
    setBusy(true);
    setError("");
    try {
      const view = await previewChange(sourceBody());
      setPreview({
        files: view.changed_files.length,
        symbols: view.changed_symbols.length,
        contracts: view.contract_changes.length,
        alignment: view.graph_alignment,
        notes: (view.diagnostics ?? []).map((item) => hideFilePaths(item.message)).filter(Boolean),
      });
    } catch (exc) {
      setPreview(null);
      setError(exc instanceof Error ? exc.message : "The change preview could not be built.");
    } finally {
      setBusy(false);
    }
  }

  async function purge() {
    setPurgeError("");
    try {
      const result = await purgeReviews();
      setConfirmPurge(false);
      setPurgeNote(`Removed ${result.removed} stored reviews. Reports were kept.`);
      setReviews(await recentReviews().catch(() => []));
    } catch (exc) {
      setPurgeError(exc instanceof Error ? exc.message : "Stored reviews could not be removed.");
    }
  }

  const commits = options?.recent_commits ?? [];
  const branches = options?.branches ?? [];

  return (
    <div className="stack-gap">
      <PageHeader
        title="Review change"
        eyebrow="Powered by Nami Boundary Gate"
        description="Analyze an actual change and determine what it affects, why it matters, what can be verified, and whether anything should block it."
        actions={confirmPurge ? (
          <div className="button-row">
            <Button kind="danger" onClick={() => void purge()}>Delete stored reviews</Button>
            <Button kind="secondary" onClick={() => setConfirmPurge(false)}>Cancel</Button>
          </div>
        ) : (
          <Button kind="danger--tertiary" onClick={() => { setPurgeNote(""); setPurgeError(""); setConfirmPurge(true); }}>Purge reviews</Button>
        )}
      />
      {confirmPurge ? <InlineNotification kind="warning" title="Purge stored reviews" subtitle="This deletes every stored review change. Reports are kept." lowContrast hideCloseButton /> : null}
      {purgeNote ? <InlineNotification kind="success" title="Reviews" subtitle={purgeNote} lowContrast hideCloseButton /> : null}
      {purgeError ? <InlineNotification kind="error" title="Reviews" subtitle={purgeError} lowContrast hideCloseButton /> : null}
      {error ? <InlineNotification kind="error" title="Review did not start" subtitle={error} lowContrast hideCloseButton /> : null}
      <Grid>
        <Column sm={4} md={4} lg={8}>
          <Dropdown
            id="review-scope"
            titleText="Review scope"
            label="Individual repository"
            items={REVIEW_SCOPES}
            itemToString={(item) => item?.text ?? ""}
            selectedItem={REVIEW_SCOPES.find((item) => item.id === reviewScope)}
            onChange={({ selectedItem }) => chooseScope(selectedItem?.id === "service_cluster" ? "service_cluster" : "repository")}
          />
        </Column>
        {reviewScope === "service_cluster" ? (
          <Column sm={4} md={4} lg={8}>
            <Dropdown
              id="review-cluster"
              titleText="Service cluster"
              label="Choose a service cluster"
              items={clusters}
              itemToString={(item) => item ? `${item.display_label || item.label} · ${item.service_count} services` : ""}
              selectedItem={selectedCluster}
              onChange={({ selectedItem }) => {
                if (selectedItem?.cluster_id) chooseCluster(selectedItem.cluster_id);
              }}
            />
            {selectedCluster?.membership_source_label ? <p className="helper">{selectedCluster.membership_source_label} · {selectedCluster.service_count} services</p> : null}
          </Column>
        ) : null}
        <Column sm={4} md={4} lg={8}>
          <Dropdown
            id="change-source"
            titleText="Change source"
            label="Choose a source"
            items={SOURCES}
            itemToString={(item) => item?.text ?? ""}
            selectedItem={source}
            onChange={({ selectedItem }) => {
              const next = new URLSearchParams(params);
              next.set("source", selectedItem?.id ?? "working_tree");
              setParams(next, { replace: true });
              setPreview(null);
            }}
          />
        </Column>
        {source.id !== "github_pr" || reviewScope === "service_cluster" ? (
          <Column sm={4} md={4} lg={8}>
            <ComboBox
              id="review-repository"
              titleText={reviewScope === "service_cluster" ? "Change origin repository" : "Repository"}
              placeholder={reviewScope === "service_cluster" ? "Choose the origin repository" : "Choose a repository"}
              items={visibleRepos}
              selectedItem={visibleRepos.find((item) => item.slug === repository) ?? null}
              itemToString={(item) => item ? (reviewScope === "service_cluster" ? serviceIdOf(item) : item.name) : ""}
              onChange={({ selectedItem }) => {
                setRepository(selectedItem?.slug ?? "");
                setPreview(null);
              }}
            />
          </Column>
        ) : null}
        {source.id === "github_pr" ? (
          <Column sm={4} md={4} lg={8}>
            <TextInput id="pr-url" labelText="Pull request URL" value={prUrl} onChange={(event) => setPrUrl(event.target.value)} />
          </Column>
        ) : null}
        {form.sourceMessage ? <Column sm={4} md={8} lg={16}><p className="helper">{form.sourceMessage}</p></Column> : null}
        {form.showClusterContext && selectedCluster ? (
          <Column sm={4} md={8} lg={16}>
            <p className="helper">Cluster review · {selectedCluster.display_label || selectedCluster.label} · {selectedCluster.service_count} services · Origin: {originLabel}</p>
            <p className="helper">Impact is evaluated only for evidenced targets within the selected cluster.</p>
          </Column>
        ) : null}
        {source.id === "commit" ? (
          <Column sm={4} md={4} lg={8}>
            <ComboBox
              id="commit"
              titleText="Commit"
              items={commits.map((item) => item.sha)}
              selectedItem={commit}
              itemToString={(item) => {
                const match = commits.find((entry) => entry.sha === item);
                return match ? `${match.short_sha} — ${match.subject}` : item ?? "";
              }}
              onChange={({ selectedItem, inputValue }) => setCommit(selectedItem || inputValue || "")}
            />
          </Column>
        ) : null}
        {source.id === "range" ? (
          <>
            <Column sm={4} md={4} lg={8}>
              <ComboBox id="base" titleText="Base branch/ref" items={branches} selectedItem={base} itemToString={(item) => item ?? ""} onChange={({ selectedItem, inputValue }) => setBase(selectedItem || inputValue || "")} />
            </Column>
            <Column sm={4} md={4} lg={8}>
              <ComboBox id="head" titleText="Head branch/ref" items={branches} selectedItem={head} itemToString={(item) => item ?? ""} onChange={({ selectedItem, inputValue }) => setHead(selectedItem || inputValue || "")} />
            </Column>
          </>
        ) : null}
      </Grid>
      <div className="review-options">
        <div>
          <RadioButtonGroup legendText="Review mode" name="review-mode" valueSelected={mode} onChange={(value) => setMode(value === "fast" || value === "exact" ? value : "auto")}>
            <RadioButton value="auto" labelText="Recommended" />
            <RadioButton value="fast" labelText="Fast" />
            <RadioButton value="exact" labelText="Exact" />
          </RadioButtonGroup>
          <p className="helper"><strong>{MODE_LABELS[mode]}. </strong>{form.modeText}</p>
        </div>
        <div>
          <Toggle id="run-verification" labelText="Run verification" toggled={form.verificationEnabled && verify} disabled={!form.verificationEnabled} onToggle={setVerify} />
          <p className="helper">{form.verificationEnabled ? `Runs ${verificationCommands.join(", ")}.` : `No verification commands are configured for ${originLabel || "the selected repository"}.`}</p>
        </div>
        {modelReady ? <Toggle id="model-assistance" labelText="Model assistance" toggled={model} onToggle={setModel} /> : null}
      </div>
      {preview ? (
        <div>
          <p className="helper">{preview.files} changed files · {preview.symbols} changed symbols · {preview.contracts} contract change{preview.contracts === 1 ? "" : "s"} · Graph alignment: {alignmentLabel(preview.alignment)}</p>
          {preview.notes.map((note) => <p key={note} className="helper">{note}</p>)}
        </div>
      ) : null}
      <div className="button-row">
        {busy ? <InlineLoading description="Working" /> : (
          <>
            <Button kind="tertiary" disabled={!form.canPreview} onClick={() => void previewSource()}>Preview change</Button>
            <Button kind="primary" disabled={!form.canReview} onClick={() => void submit()}>Review change</Button>
          </>
        )}
      </div>
      <h2 className="section-title">Recent reviews</h2>
      {reviews.length === 0 ? (
        <NamiEmptyState title="No stored reviews" description="Completed reviews appear here." />
      ) : (
        <NamiDataTable
          columns={[
            { key: "decision", header: "Decision", render: (row) => <StatusTag status={row.decision} /> },
            { key: "change", header: "Change", render: (row) => <Link to={`/review/${row.id}`}>{row.change}</Link> },
            { key: "scope", header: "Scope" },
            { key: "service", header: "Origin" },
            { key: "findings", header: "Findings" },
            { key: "when", header: "When" },
          ]}
          rows={reviews.map((item) => ({
            id: item.review_id,
            decision: item.decision,
            change: changeLabel(item),
            scope: scopeColumnLabel(item.review_scope, item.review_scope_label),
            service: repos.find((repo) => repo.slug === item.service)?.name || item.service || "—",
            findings: String(item.finding_count),
            when: item.completed_at || "—",
          }))}
          emptyTitle="No stored reviews"
          emptyDescription="Completed reviews appear here."
        />
      )}
    </div>
  );
}

function serviceIdOf(repo: { slug: string; service_id?: string } | undefined): string {
  if (!repo) return "";
  return repo.service_id || repo.slug;
}

function changeLabel(item: { source_kind: string; head_sha?: string; base_sha?: string }): string {
  const name = SOURCE_LABELS[item.source_kind] || "Change";
  const head = (item.head_sha || "").slice(0, 7);
  const base = (item.base_sha || "").slice(0, 7);
  if (item.source_kind === "commit" && head) return `${name} ${head}`;
  if (item.source_kind === "range" && base && head) return `${name} ${base}…${head}`;
  return name;
}

function alignmentLabel(alignment: string): string {
  if (alignment === "exact") return "Current";
  if (alignment === "compatible") return "Compatible";
  if (alignment === "stale") return "Stale";
  if (alignment === "unavailable") return "Unavailable";
  return alignment || "Unavailable";
}
