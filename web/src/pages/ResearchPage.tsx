import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button, Column, ComboBox, Dropdown, Grid, InlineNotification, NumberInput, TextInput } from "@carbon/react";
import { clusterCatalog, nrmCapabilities, runHybridImpact, type AppState, type HybridImpactResponse } from "../api";
import { NamiDataTable } from "../components/NamiDataTable";
import { PageHeader } from "../components/PageHeader";
import { relativePath, symbolName } from "../labels";

const EXPERIMENTS = ["hybrid", "structural_only", "temporal_only"];

export function ResearchPage({ snapshot }: { snapshot: AppState | null }) {
  const [params, setParams] = useSearchParams();
  const [capabilities, setCapabilities] = useState<Awaited<ReturnType<typeof nrmCapabilities>> | null>(null);
  const [files, setFiles] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [result, setResult] = useState<HybridImpactResponse | null>(null);
  const services = snapshot?.cluster?.services?.map((item) => item.id) ?? [];
  const service = params.get("service") || services[0] || "";
  const file = params.get("file") || "";
  const experiment = params.get("experiment") || "hybrid";
  const cutoff = params.get("as_of") || "";
  const provider = params.get("provider") || "commit_history";
  const fusion = params.get("fusion") || "raw_weighted";
  const pathScoring = params.get("path_scoring") || "coverage_adjusted";
  const lag = params.get("lag") || "one_to_one";
  const alpha = Number(params.get("alpha") || capabilities?.defaults.structural_weight || 0.7);
  const beta = fusion === "rank_normalized" ? Math.max(0, 1 - alpha) : Number(params.get("beta") || capabilities?.defaults.temporal_weight || 0.3);
  const windowDays = Number(params.get("window") || capabilities?.defaults.cross_repo_window_days || 7);
  const weightError = alpha < 0 || beta < 0 || alpha > 1 || beta > 1 ? "Structural weight α and temporal weight β must each be between 0 and 1. They are not normalized silently." : "";

  useEffect(() => {
    void nrmCapabilities().then(setCapabilities).catch(() => setError("Research capabilities could not be loaded."));
  }, []);

  useEffect(() => {
    if (!service) return;
    const controller = new AbortController();
    void clusterCatalog(service, controller.signal)
      .then((payload) => setFiles(payload.files.map((item) => item.path)))
      .catch(() => setFiles([]));
    return () => controller.abort();
  }, [service]);

  function update(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key === "service") next.delete("file");
    setParams(next, { replace: true });
  }

  async function run() {
    if (weightError) {
      setError(weightError);
      return;
    }
    setError("");
    try {
      setResult(await runHybridImpact({
        service,
        file,
        experiment,
        as_of: cutoff || null,
        temporal_provider: provider,
        fusion_method: fusion,
        path_scoring: pathScoring,
        lag_matching: lag,
        weights: { structural: alpha, temporal: beta },
        cross_repo_window_days: windowDays,
      }));
    } catch (exc) {
      setResult(null);
      setError(exc instanceof Error ? exc.message : "The evaluation run failed.");
    }
  }

  return (
    <div className="stack-gap">
      <PageHeader
        title="Research"
        description="Research controls affect this evaluation request only. Ranking scores are not probabilities."
      />
      <p className="helper">These controls do not change the product defaults stored by the server, NRM weights, or review feedback.</p>
      {error || weightError ? <InlineNotification kind="error" title="Research" subtitle={error || weightError} lowContrast hideCloseButton /> : null}
      {capabilities ? (
        <p className="helper">
          NRM {capabilities.module_version}. Default weights structural {capabilities.defaults.structural_weight.toFixed(2)}, temporal {capabilities.defaults.temporal_weight.toFixed(2)}.
          Cross-repository window {capabilities.defaults.cross_repo_window_days} days.
        </p>
      ) : null}
      <Grid>
        <Column sm={4} md={4} lg={4}>
          <Dropdown id="research-service" titleText="Service" label="Service" items={services} itemToString={(item) => item ?? ""} selectedItem={service} onChange={({ selectedItem }) => update("service", selectedItem ?? "")} />
        </Column>
        <Column sm={4} md={4} lg={8}>
          <ComboBox id="research-file" titleText="File" items={files} selectedItem={files.includes(file) ? file : null} itemToString={(item) => item ?? ""} onChange={({ selectedItem }) => update("file", selectedItem ?? "")} />
        </Column>
        <Column sm={4} md={4} lg={4}>
          <Dropdown id="research-experiment" titleText="Experiment" label="Combined" items={EXPERIMENTS} itemToString={(item) => item ?? ""} selectedItem={experiment} onChange={({ selectedItem }) => update("experiment", selectedItem ?? "hybrid")} />
        </Column>
        <Column sm={4} md={4} lg={4}>
          <Dropdown id="research-provider" titleText="Temporal provider" label="Provider" items={(capabilities?.temporal_providers ?? []).map((item) => item.id)} itemToString={(item) => item ?? ""} selectedItem={provider} onChange={({ selectedItem }) => update("provider", selectedItem ?? "commit_history")} />
        </Column>
        <Column sm={4} md={4} lg={4}>
          <NumberInput id="research-alpha" label="Structural weight α" min={0} max={1} step={0.05} value={alpha} onChange={(_event, { value }) => update("alpha", String(value))} />
        </Column>
        <Column sm={4} md={4} lg={4}>
          <NumberInput id="research-beta" label="Temporal weight β" min={0} max={1} step={0.05} value={beta} disabled={fusion === "rank_normalized"} onChange={(_event, { value }) => update("beta", String(value))} helperText={fusion === "rank_normalized" ? "Rank-normalized fusion uses β = 1 − α." : "Raw weighted fusion keeps the weights you enter."} />
        </Column>
        <Column sm={4} md={4} lg={4}>
          <Dropdown id="research-fusion" titleText="Fusion method" label="Fusion" items={capabilities?.fusion_methods ?? ["raw_weighted", "rank_normalized"]} itemToString={(item) => item ?? ""} selectedItem={fusion} onChange={({ selectedItem }) => update("fusion", selectedItem ?? "raw_weighted")} />
        </Column>
        <Column sm={4} md={4} lg={4}>
          <Dropdown id="research-path" titleText="Path scoring" label="Path scoring" items={capabilities?.path_scoring ?? ["coverage_adjusted", "raw"]} itemToString={(item) => item ?? ""} selectedItem={pathScoring} onChange={({ selectedItem }) => update("path_scoring", selectedItem ?? "coverage_adjusted")} />
        </Column>
        <Column sm={4} md={4} lg={4}>
          <Dropdown id="research-lag" titleText="Lag matching" label="Lag matching" items={capabilities?.lag_matching ?? ["one_to_one", "reusable_target"]} itemToString={(item) => item ?? ""} selectedItem={lag} onChange={({ selectedItem }) => update("lag", selectedItem ?? "one_to_one")} />
        </Column>
        <Column sm={4} md={4} lg={4}>
          <NumberInput id="research-window" label="Cross-repository window" min={0} max={90} step={1} value={windowDays} onChange={(_event, { value }) => update("window", String(value))} />
        </Column>
        <Column sm={4} md={4} lg={8}>
          <TextInput id="research-cutoff" labelText="History cutoff" value={cutoff} onChange={(event) => update("as_of", event.target.value)} helperText="Exact causal cutoff. Leave empty to use the current instant." />
        </Column>
      </Grid>
      <Button kind="primary" disabled={!service || !file || Boolean(weightError)} onClick={() => void run()}>Run evaluation</Button>
      {result ? (
        <>
          <div className="button-row">
            <Button kind="ghost" size="sm" onClick={() => downloadResearch("research.json", JSON.stringify(result.candidates, null, 2), "application/json")}>JSON</Button>
            <Button kind="ghost" size="sm" onClick={() => downloadResearch("research.csv", researchCsv(result), "text/csv")}>CSV</Button>
          </div>
          <p className="helper">
            Temporal causal validity {String(result.causal_validity?.temporal)}. Structural causal validity {String(result.causal_validity?.structural)}. Overall causal validity {String(result.causal_validity?.overall)}. Provider {result.temporal_provider}. Cutoff {result.temporal_cutoff || "none"}.
          </p>
          <NamiDataTable
            columns={[
              { key: "code", header: "Code" },
              { key: "file", header: "File" },
              { key: "service_id", header: "Service" },
              { key: "hybrid", header: "NRM" },
              { key: "structural", header: "Structural" },
              { key: "temporal", header: "Temporal" },
              { key: "origin", header: "Origin" },
            ]}
            rows={result.candidates.map((item, index) => ({
              id: `${item.file_path}-${index}`,
              code: symbolName(item.name || item.file_path),
              file: relativePath(item.file_path) || item.file_path,
              service_id: item.service_id,
              hybrid: item.hybrid_score.toFixed(3),
              structural: item.structural.score.toFixed(3),
              temporal: item.temporal.score.toFixed(3),
              origin: item.evidence_origin,
            }))}
            emptyTitle="No ranked files"
            emptyDescription="This evaluation returned no candidates."
          />
        </>
      ) : null}
    </div>
  );
}

function downloadResearch(filename: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function researchCsv(result: HybridImpactResponse): string {
  const lines = ["service,file,nami_rank,structural,temporal,origin"];
  for (const item of result.candidates) {
    lines.push([item.service_id, item.file_path, item.hybrid_score.toFixed(3), item.structural.score.toFixed(3), item.temporal.score.toFixed(3), item.evidence_origin].join(","));
  }
  return lines.join("\n");
}
