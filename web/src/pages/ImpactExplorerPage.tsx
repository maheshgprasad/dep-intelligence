import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Accordion,
  AccordionItem,
  Button,
  Column,
  ComboBox,
  ContentSwitcher,
  DatePicker,
  DatePickerInput,
  Dropdown,
  Grid,
  InlineLoading,
  InlineNotification,
  Switch,
} from "@carbon/react";
import { clusterCatalog, refreshIntelligence, runHybridImpact, type AppState, type HybridCandidate, type HybridImpactResponse } from "../api";
import { FocusedImpactPath } from "../components/FocusedImpactPath";
import { NamiDataTable } from "../components/NamiDataTable";
import { PageHeader } from "../components/PageHeader";
import { ScoreHelp } from "../components/ScoreHelp";
import { StatusTag } from "../components/StatusTag";
import { hideFilePaths, relativePath, repoSymbol, symbolName } from "../labels";

const MODES = [
  { id: "hybrid", text: "Combined" },
  { id: "structural_only", text: "Structural only" },
  { id: "temporal_only", text: "Historical only" },
];

const SCENARIOS = [
  { id: "none", text: "None" },
  { id: "http_unavailable", text: "HTTP service unavailable" },
  { id: "grpc_unavailable", text: "gRPC service unavailable" },
];

export function ImpactExplorerPage({ snapshot }: { snapshot: AppState | null }) {
  const [params, setParams] = useSearchParams();
  const services = snapshot?.cluster?.services ?? [];
  const service = params.get("service") || services[0]?.id || "";
  const file = params.get("file") || "";
  const analyze = params.get("analyze") === "contract" ? "contract" : "code";
  const [files, setFiles] = useState<string[]>([]);
  const [contracts, setContracts] = useState<{ id: string; label: string }[]>([]);
  const [contract, setContract] = useState("");
  const [evidence, setEvidence] = useState(MODES[0]);
  const [asOf, setAsOf] = useState("");
  const [scenario, setScenario] = useState(SCENARIOS[0]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const [result, setResult] = useState<HybridImpactResponse | null>(null);

  function invalidate() {
    setResult(null);
    setError("");
    setStale(false);
  }

  useEffect(() => {
    invalidate();
  }, [service, file, contract, analyze, evidence.id, asOf, scenario.id]);

  useEffect(() => {
    if (!service) return;
    const controller = new AbortController();
    let current = true;
    void clusterCatalog(service, controller.signal)
      .then((payload) => {
        if (!current) return;
        setFiles(payload.files.map((item) => item.path));
        setContracts(payload.contracts.map((item) => ({ id: item.id, label: item.name })));
      })
      .catch(() => {
        if (!current) return;
        setFiles([]);
        setContracts([]);
      });
    return () => {
      current = false;
      controller.abort();
    };
  }, [service]);

  function setQuery(next: Record<string, string>) {
    const query = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value) query.set(key, value);
      else query.delete(key);
    }
    setParams(query, { replace: true });
  }

  async function analyzeImpact() {
    setBusy(true);
    setError("");
    setStale(false);
    const operational = scenario.id === "http_unavailable" || scenario.id === "grpc_unavailable";
    const body: Record<string, unknown> = {
      service,
      experiment: evidence.id,
      as_of: asOf || null,
      mode: operational ? "operational_failure" : analyze === "contract" ? "contract_change" : "code_change",
    };
    if (operational) body.scenario = scenario.id;
    if (analyze === "contract") body.contract_id = contract;
    else body.file = file;
    try {
      setResult(await runHybridImpact(body));
    } catch (exc) {
      const status = (exc as { status?: number }).status;
      setStale(status === 409);
      setError(status === 409 ? "The published intelligence is older than this selection." : exc instanceof Error ? exc.message : "Impact could not be analyzed.");
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  const selectedContract = contracts.find((item) => item.id === contract) ?? null;

  return (
    <div className="stack-gap">
      <PageHeader
        title="Impact explorer"
        eyebrow="Powered by Nami Rank Module"
        description="See which files and services may be affected by a code or contract change."
      />
      {error ? (
        <InlineNotification
          kind={stale ? "warning" : "error"}
          title={stale ? "Published intelligence is stale" : "Impact analysis failed"}
          subtitle={error}
          lowContrast
          hideCloseButton
        />
      ) : null}
      {stale ? (
        <Button kind="tertiary" onClick={() => void refreshIntelligence()}>Refresh intelligence</Button>
      ) : null}
      <ContentSwitcher
        selectedIndex={analyze === "contract" ? 1 : 0}
        onChange={({ index }) => {
          invalidate();
          setQuery({ analyze: index === 1 ? "contract" : "code" });
        }}
      >
        <Switch>Code / file</Switch>
        <Switch>API contract</Switch>
      </ContentSwitcher>
      <Grid>
        <Column sm={4} md={4} lg={5}>
          <ComboBox
            id="impact-service"
            titleText="Service"
            items={services.map((item) => item.id)}
            selectedItem={service}
            itemToString={(item) => item ?? ""}
            onChange={({ selectedItem }) => {
              setContract("");
              invalidate();
              setQuery({ service: selectedItem ?? "", file: "", contract: "" });
            }}
          />
        </Column>
        <Column sm={4} md={4} lg={7}>
          {analyze === "code" ? (
            <ComboBox
              id="impact-file"
              titleText="File"
              items={files}
              selectedItem={files.includes(file) ? file : null}
              itemToString={(item) => relativePath(item || "") || item || ""}
              onChange={({ selectedItem }) => {
                invalidate();
                setQuery({ file: selectedItem ?? "" });
              }}
            />
          ) : (
            <Dropdown
              id="impact-contract"
              titleText="Contract"
              label="Choose a contract"
              items={contracts}
              itemToString={(item) => item?.label ?? ""}
              selectedItem={selectedContract}
              onChange={({ selectedItem }) => {
                setContract(selectedItem?.id ?? "");
                invalidate();
                setQuery({ contract: selectedItem?.id ?? "" });
              }}
            />
          )}
        </Column>
        <Column sm={4} md={4} lg={4}>
          <div className="button-row">
            {busy ? <InlineLoading description="Analyzing impact" /> : (
              <Button kind="primary" disabled={analyze === "code" ? !file : !contract} onClick={() => void analyzeImpact()}>
                Analyze impact
              </Button>
            )}
          </div>
        </Column>
      </Grid>
      <Accordion>
        <AccordionItem title="Advanced options">
          <Grid>
            <Column sm={4} md={4} lg={5}>
              <Dropdown
                id="evidence-mode"
                titleText="Evidence mode"
                label="Combined"
                items={MODES}
                itemToString={(item) => item?.text ?? ""}
                selectedItem={evidence}
                onChange={({ selectedItem }) => {
                  if (!selectedItem) return;
                  invalidate();
                  setEvidence(selectedItem);
                }}
              />
            </Column>
            <Column sm={4} md={4} lg={5}>
              <DatePicker
                datePickerType="single"
                onChange={(dates) => {
                  const date = dates?.[0];
                  invalidate();
                  if (!date) {
                    setAsOf("");
                    return;
                  }
                  setAsOf(new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate(), 23, 59, 59)).toISOString());
                }}
              >
                <DatePickerInput id="history-cutoff" labelText="History cutoff" placeholder="yyyy-mm-dd" />
              </DatePicker>
              {asOf ? <p className="helper">Cutoff {asOf}. Events after this instant stay hidden.</p> : null}
            </Column>
            <Column sm={4} md={4} lg={6}>
              <Dropdown
                id="operational-scenario"
                titleText="Operational scenario"
                label="None"
                items={SCENARIOS}
                itemToString={(item) => item?.text ?? ""}
                selectedItem={scenario}
                onChange={({ selectedItem }) => {
                  if (!selectedItem) return;
                  invalidate();
                  setScenario(selectedItem);
                }}
              />
            </Column>
          </Grid>
        </AccordionItem>
      </Accordion>
      {result ? <ImpactResult result={result} /> : null}
    </div>
  );
}

function ImpactResult({ result }: { result: HybridImpactResponse }) {
  return (
    <NamiDataTable
      title="Downstream candidates"
      description={result.score_meaning}
      columns={[
        { key: "rank", header: "Rank" },
        { key: "code", header: "Affected code" },
        { key: "file", header: "File" },
        { key: "service_id", header: "Service" },
        { key: "origin", header: "Evidence", render: (row) => <StatusTag status={originKind(row.evidence_origin)} label={originLabel(row.evidence_origin)} /> },
        { key: "score", header: <ScoreHelp /> },
      ]}
      rows={result.candidates.map((item, index) => ({ ...item, id: `${item.service_id}-${item.file_path}-${index}`, rank: index + 1, code: symbolName(item.name || item.file_path), file: relativePath(item.file_path) || item.file_path, score: item.hybrid_score.toFixed(3) }))}
      emptyTitle="No downstream impact found"
      emptyDescription="No files met the current impact criteria."
      emptyAction={{ label: "Change analysis", href: "/impact" }}
      renderExpanded={(row) => <CandidateDetail row={row} />}
    />
  );
}

function CandidateDetail({ row }: { row: HybridCandidate & { id: string } }) {
  return (
    <div className="stack-gap">
      <div>
        <h3 className="section-title">Why this is ranked</h3>
        <ul>{(row.why_ranked.length ? row.why_ranked : ["Ranked from the available evidence."]).map((item) => <li key={item}>{hideFilePaths(item)}</li>)}</ul>
      </div>
      <div>
        <h3 className="section-title">Strongest structural path</h3>
        <FocusedImpactPath steps={row.structural.path} />
      </div>
      <div>
        <h3 className="section-title">Historical evidence</h3>
        <p>{hideFilePaths(row.temporal.observation || "No historical support was recorded.")}</p>
      </div>
      <div>
        <h3 className="section-title">Affected symbols</h3>
        <p>{(row.structural.impacted_symbols ?? []).map((item) => repoSymbol(row.service_id, symbolName(item.name))).join(", ") || symbolName(row.name)}</p>
        {relativePath(row.file_path) ? <p className="helper">{relativePath(row.file_path)}</p> : null}
      </div>
      <Accordion>
        <AccordionItem title="Technical scores">
          <p>NRM {row.hybrid_score.toFixed(3)} · Structural {row.structural.score.toFixed(3)} · Temporal {row.temporal.score.toFixed(3)}</p>
          <p className="helper">Ranking scores are not probabilities of failure.</p>
        </AccordionItem>
      </Accordion>
    </div>
  );
}

function originKind(origin: string): string {
  if (origin === "hybrid" || origin === "both") return "both";
  if (origin.includes("temporal")) return "history";
  return "structure";
}

function originLabel(origin: string): string {
  if (origin === "hybrid" || origin === "both") return "Structure + history";
  if (origin.includes("temporal")) return "History";
  return "Structure";
}
