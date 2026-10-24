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
import { previewChange, recentReviews, reviewConfig, reviewSourceOptions, runReview, type AppState } from "../api";
import { hideFilePaths } from "../labels";
import { NamiDataTable } from "../components/NamiDataTable";
import { NamiEmptyState } from "../components/NamiEmptyState";
import { PageHeader } from "../components/PageHeader";
import { StatusTag } from "../components/StatusTag";

const SOURCES = [
  { id: "working_tree", text: "Working tree" },
  { id: "staged", text: "Staged changes" },
  { id: "commit", text: "Commit" },
  { id: "range", text: "Branch / commit range" },
  { id: "github_pr", text: "Pull request" },
];

const MODE_HELP = [
  { id: "auto", label: "Recommended", text: "Uses the published graph when it matches this change, and builds a private graph when it does not." },
  { id: "fast", label: "Fast", text: "Uses the published intelligence snapshot." },
  { id: "exact", label: "Exact", text: "Builds graph evidence for this revision and does not publish it." },
] as const;

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
  const repos = (snapshot?.repositories ?? []).filter((repo) => repo.checkout);
  const [repository, setRepository] = useState(params.get("repository") || repos[0]?.checkout || "");
  const [commit, setCommit] = useState("");
  const [base, setBase] = useState("");
  const [head, setHead] = useState("");
  const [prUrl, setPrUrl] = useState("");
  const [mode, setMode] = useState<"auto" | "fast" | "exact">("auto");
  const [verify, setVerify] = useState(true);
  const [model, setModel] = useState(false);
  const [modelReady, setModelReady] = useState(false);
  const [verificationCommands, setVerificationCommands] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [options, setOptions] = useState<Awaited<ReturnType<typeof reviewSourceOptions>> | null>(null);
  const [preview, setPreview] = useState<{ files: number; symbols: number; contracts: number; alignment: string; notes: string[] } | null>(null);
  const [reviews, setReviews] = useState<Awaited<ReturnType<typeof recentReviews>>>([]);

  const defaultRepository = repos[0]?.checkout || "";
  useEffect(() => {
    if (!repository && defaultRepository) setRepository(defaultRepository);
  }, [repository, defaultRepository]);

  useEffect(() => {
    void reviewConfig(repository).then((config) => {
      setModelReady(config.model_configured);
      setVerificationCommands(config.verification?.commands ?? []);
    }).catch(() => {
      setModelReady(false);
      setVerificationCommands([]);
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

  function sourceBody() {
    return {
      mode,
      verify,
      model: modelReady && model ? { enabled: true } : {},
      service: "",
      source: {
        kind: source.id,
        repository: source.id === "github_pr" ? "" : repository,
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

  const commits = options?.recent_commits ?? [];
  const branches = options?.branches ?? [];

  return (
    <div className="stack-gap">
      <PageHeader
        title="Review change"
        eyebrow="Powered by Nami Boundary Gate"
        description="Analyze an actual change and determine what it affects, why it matters, what can be verified, and whether anything should block it."
      />
      {error ? <InlineNotification kind="error" title="Review did not start" subtitle={error} lowContrast hideCloseButton /> : null}
      <Grid>
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
        {source.id !== "github_pr" ? (
          <Column sm={4} md={4} lg={8}>
            <ComboBox
              id="review-repository"
              titleText="Repository"
              placeholder="Choose a repository"
              items={repos}
              selectedItem={repos.find((item) => item.checkout === repository) ?? null}
              itemToString={(item) => item?.name ?? ""}
              onChange={({ selectedItem }) => {
                setRepository(selectedItem?.checkout ?? "");
                setPreview(null);
              }}
            />
          </Column>
        ) : (
          <Column sm={4} md={4} lg={8}>
            <TextInput id="pr-url" labelText="Pull request URL" value={prUrl} onChange={(event) => setPrUrl(event.target.value)} />
          </Column>
        )}
        {source.id === "working_tree" && options ? <Column sm={4} md={8} lg={16}><p className="helper">{options.dirty_files} changed files on {options.current_branch || "the current branch"}</p></Column> : null}
        {source.id === "staged" && options ? <Column sm={4} md={8} lg={16}><p className="helper">{options.staged_files} staged files</p></Column> : null}
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
          <ul className="mode-explanations">
            {MODE_HELP.map((item) => (
              <li key={item.id} className={item.id === mode ? "is-selected" : undefined}>
                <strong>{item.label}. </strong>{item.text}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <Toggle id="run-verification" labelText="Run verification" toggled={verify} onToggle={setVerify} />
          <p className="helper">{verificationCommands.length ? `Runs ${verificationCommands.join(", ")}.` : "No verification commands are configured, so this option does not run a check."}</p>
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
            <Button kind="tertiary" disabled={!repository && source.id !== "github_pr"} onClick={() => void previewSource()}>Preview change</Button>
            <Button kind="primary" onClick={() => void submit()}>Review change</Button>
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
            { key: "service", header: "Service" },
            { key: "findings", header: "Findings" },
            { key: "when", header: "When" },
          ]}
          rows={reviews.map((item) => ({
            id: item.review_id,
            decision: item.decision,
            change: changeLabel(item),
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
