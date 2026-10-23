import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Information } from "@carbon/icons-react";
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
  Toggletip,
  ToggletipButton,
  ToggletipContent,
} from "@carbon/react";
import { reviewConfig, runReview, type Snapshot } from "../api";
import { PageHeader } from "../components/PageHeader";

const SOURCES = [
  { id: "working_tree", text: "Working tree" },
  { id: "staged", text: "Staged changes" },
  { id: "commit", text: "Commit" },
  { id: "range", text: "Branch / commit range" },
  { id: "github_pr", text: "Pull request" },
];

export function ReviewPage({ snapshot }: { snapshot: Snapshot | null }) {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const sourceId = params.get("source") || "working_tree";
  const source = SOURCES.find((item) => item.id === sourceId) ?? SOURCES[0];
  const repos = (snapshot?.graphs?.repos ?? [])
    .map((repo) => ({ id: repo.checkout || repo.name, label: repo.name }))
    .filter((repo) => repo.id);
  const [repository, setRepository] = useState(params.get("repository") || repos[0]?.id || "");
  const [commit, setCommit] = useState("");
  const [base, setBase] = useState("");
  const [head, setHead] = useState("");
  const [prUrl, setPrUrl] = useState("");
  const [mode, setMode] = useState<"fast" | "exact">("fast");
  const [verify, setVerify] = useState(true);
  const [model, setModel] = useState(false);
  const [modelReady, setModelReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void reviewConfig(repository).then((config) => setModelReady(config.model_configured)).catch(() => setModelReady(false));
  }, [repository]);

  async function submit() {
    setBusy(true);
    setError("");
    const body = {
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
    try {
      const report = await runReview(body);
      if (report.job_id) navigate(`/review/${report.job_id}?job=1`);
      else if (report.review_id) navigate(`/review/${report.review_id}`);
      else setError("The review finished without a stored result.");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "The review could not be started.");
    } finally {
      setBusy(false);
    }
  }

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
              selectedItem={repos.find((item) => item.id === repository) ?? null}
              itemToString={(item) => item?.label ?? ""}
              onChange={({ selectedItem }) => setRepository(selectedItem?.id ?? "")}
            />
          </Column>
        ) : (
          <Column sm={4} md={4} lg={8}>
            <TextInput id="pr-url" labelText="Pull request URL" value={prUrl} onChange={(event) => setPrUrl(event.target.value)} />
          </Column>
        )}
        {source.id === "commit" ? (
          <Column sm={4} md={4} lg={8}>
            <TextInput id="commit" labelText="Commit" value={commit} onChange={(event) => setCommit(event.target.value)} />
          </Column>
        ) : null}
        {source.id === "range" ? (
          <>
            <Column sm={4} md={4} lg={8}>
              <TextInput id="base" labelText="Base" value={base} onChange={(event) => setBase(event.target.value)} />
            </Column>
            <Column sm={4} md={4} lg={8}>
              <TextInput id="head" labelText="Head" value={head} onChange={(event) => setHead(event.target.value)} />
            </Column>
          </>
        ) : null}
      </Grid>
      <div className="review-options">
        <div>
          <RadioButtonGroup
            legendText="Review mode"
            name="review-mode"
            valueSelected={mode}
            onChange={(value) => setMode(value === "exact" ? "exact" : "fast")}
          >
            <RadioButton value="fast" labelText="Fast" />
            <RadioButton value="exact" labelText="Exact" />
          </RadioButtonGroup>
          <p className="helper">{mode === "fast" ? "Uses the most recent published Nami graph." : "Builds change-specific graph evidence for the reviewed revision."}</p>
          <Toggletip align="bottom-left">
            <ToggletipButton label="What Exact review does">
              <Information size={16} />
            </ToggletipButton>
            <ToggletipContent>
              <p>Fast reads the published snapshot. Exact builds a private graph for the reviewed revision and does not publish it.</p>
            </ToggletipContent>
          </Toggletip>
        </div>
        <Toggle id="run-verification" labelText="Run verification" toggled={verify} onToggle={setVerify} />
        {modelReady ? <Toggle id="model-assistance" labelText="Model assistance" toggled={model} onToggle={setModel} /> : null}
      </div>
      {busy ? <InlineLoading description="Starting review" /> : <Button kind="primary" onClick={() => void submit()}>Review change</Button>}
    </div>
  );
}
