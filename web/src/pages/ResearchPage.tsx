import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button, Column, Dropdown, Grid, InlineNotification, TextInput } from "@carbon/react";
import { nrmCapabilities, runHybridImpact, type HybridImpactResponse, type Snapshot } from "../api";
import { NamiDataTable } from "../components/NamiDataTable";
import { PageHeader } from "../components/PageHeader";
import { repoSymbol, symbolName } from "../labels";

const EXPERIMENTS = ["hybrid", "structural_only", "temporal_only"];

export function ResearchPage({ snapshot }: { snapshot: Snapshot | null }) {
  const [params, setParams] = useSearchParams();
  const [capabilities, setCapabilities] = useState<Awaited<ReturnType<typeof nrmCapabilities>> | null>(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState<HybridImpactResponse | null>(null);
  const services = snapshot?.cluster?.services?.map((item) => item.id) ?? [];
  const service = params.get("service") || services[0] || "";
  const file = params.get("file") || "";
  const experiment = params.get("experiment") || "hybrid";
  const cutoff = params.get("as_of") || "";

  useEffect(() => {
    void nrmCapabilities().then(setCapabilities).catch(() => setError("Research capabilities could not be loaded."));
  }, []);

  function update(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  async function run() {
    setError("");
    try {
      setResult(await runHybridImpact({ service, file, experiment, as_of: cutoff || null }));
    } catch (exc) {
      setResult(null);
      setError(exc instanceof Error ? exc.message : "The evaluation run failed.");
    }
  }

  return (
    <div className="stack-gap">
      <PageHeader
        title="Research"
        description="Research controls affect analysis and evaluation views. Ranking scores are not probabilities."
      />
      <p className="helper">These controls are for evaluation. They do not change the product defaults stored by the server.</p>
      {error ? <InlineNotification kind="error" title="Research" subtitle={error} lowContrast hideCloseButton /> : null}
      {capabilities ? (
        <p className="helper">
          NRM {capabilities.module_version}. Default weights structural {capabilities.defaults.structural_weight.toFixed(2)}, temporal {capabilities.defaults.temporal_weight.toFixed(2)}.
          Providers: {capabilities.temporal_providers.map((item) => `${item.id}${item.available ? "" : " (unavailable)"}`).join(", ")}.
        </p>
      ) : null}
      <Grid>
        <Column sm={4} md={4} lg={4}>
          <Dropdown id="research-service" titleText="Service" label="Service" items={services} itemToString={(item) => item ?? ""} selectedItem={service} onChange={({ selectedItem }) => update("service", selectedItem ?? "")} />
        </Column>
        <Column sm={4} md={4} lg={4}>
          <TextInput id="research-file" labelText="Symbol" value={file} onChange={(event) => update("file", event.target.value)} />
        </Column>
        <Column sm={4} md={4} lg={4}>
          <Dropdown id="research-experiment" titleText="Experiment" label="Combined" items={EXPERIMENTS} itemToString={(item) => item ?? ""} selectedItem={experiment} onChange={({ selectedItem }) => update("experiment", selectedItem ?? "hybrid")} />
        </Column>
        <Column sm={4} md={4} lg={4}>
          <TextInput id="research-cutoff" labelText="History cutoff (ISO)" value={cutoff} onChange={(event) => update("as_of", event.target.value)} helperText="Exact causal cutoff. Leave empty to use the current instant." />
        </Column>
      </Grid>
      <Button kind="primary" disabled={!service || !file} onClick={() => void run()}>Run evaluation</Button>
      {result ? (
        <>
          <p className="helper">
            Causal validity: temporal {String(result.causal_validity?.temporal)} · structural {String(result.causal_validity?.structural)} · overall {String(result.causal_validity?.overall)}. Provider {result.temporal_provider}. Cutoff {result.temporal_cutoff || "none"}.
          </p>
          <NamiDataTable
            columns={[
              { key: "code", header: "Code" },
              { key: "service_id", header: "Service" },
              { key: "hybrid", header: "NRM" },
              { key: "structural", header: "Structural" },
              { key: "temporal", header: "Temporal" },
              { key: "origin", header: "Origin" },
            ]}
            rows={result.candidates.map((item, index) => ({
              id: `${item.file_path}-${index}`,
              code: repoSymbol(item.service_id, symbolName(item.name || item.file_path)),
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
