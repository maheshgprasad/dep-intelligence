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
import { clusterGraph, runHybridImpact, type HybridCandidate, type HybridImpactResponse, type Snapshot } from "../api";
import { FocusedImpactPath } from "../components/FocusedImpactPath";
import { NamiDataTable } from "../components/NamiDataTable";
import { PageHeader } from "../components/PageHeader";
import { ScoreHelp } from "../components/ScoreHelp";
import { StatusTag } from "../components/StatusTag";
import { hideFilePaths, repoSymbol, symbolName } from "../labels";

const MODES = [
  { id: "hybrid", text: "Combined" },
  { id: "structural_only", text: "Structural only" },
  { id: "temporal_only", text: "Historical only" },
];

export function ImpactExplorerPage({ snapshot }: { snapshot: Snapshot | null }) {
  const [params, setParams] = useSearchParams();
  const services = snapshot?.cluster?.services ?? [];
  const service = params.get("service") || services[0]?.id || "";
  const file = params.get("file") || "";
  const analyze = params.get("analyze") === "contract" ? "contract" : "code";
  const [files, setFiles] = useState<string[]>([]);
  const [contracts, setContracts] = useState<{ id: string; label: string }[]>([]);
  const [contract, setContract] = useState(params.get("contract") || "");
  const [evidence, setEvidence] = useState(MODES[0]);
  const [asOf, setAsOf] = useState("");
  const [scenario, setScenario] = useState("none");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const [result, setResult] = useState<HybridImpactResponse | null>(null);

  useEffect(() => {
    if (!service) return;
    void clusterGraph({ service, limit: 2000 }).then((payload) => {
      const paths = [...new Set(payload.page.nodes.map((node) => node.file_path).filter(Boolean))].sort();
      setFiles(paths);
      setContracts(
        payload.page.nodes
          .filter((node) => node.category.includes("contract"))
          .map((node) => ({ id: node.key, label: repoSymbol(node.service_id, symbolName(node.name || node.qualified_name)) })),
      );
    }).catch(() => {
      setFiles([]);
      setContracts([]);
    });
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
    const body: Record<string, unknown> = {
      service,
      experiment: evidence.id,
      as_of: asOf || null,
      mode: analyze === "contract" ? "contract_change" : scenario === "operational" ? "operational_failure" : "code_change",
    };
    if (analyze === "contract") body.contract_id = contract;
    else body.file = file;
    try {
      setResult(await runHybridImpact(body));
    } catch (exc) {
      const status = (exc as { status?: number }).status;
      setStale(status === 409);
      setError(exc instanceof Error ? exc.message : "Impact could not be analyzed.");
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack-gap">
      <PageHeader
        title="Impact explorer"
        eyebrow="Powered by Nami Rank Module"
        description="See which files and services may be affected by a code or contract change."
      />
      {error ? (
        <InlineNotification kind={stale ? "warning" : "error"} title={stale ? "Graph is stale" : "Impact analysis failed"} subtitle={error} lowContrast hideCloseButton />
      ) : null}
      <ContentSwitcher
        selectedIndex={analyze === "contract" ? 1 : 0}
        onChange={({ index }) => setQuery({ analyze: index === 1 ? "contract" : "code" })}
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
            onChange={({ selectedItem }) => setQuery({ service: selectedItem ?? "", file: "" })}
          />
        </Column>
        <Column sm={4} md={4} lg={7}>
          {analyze === "code" ? (
            <ComboBox
              id="impact-file"
              titleText="Symbol"
              items={files}
              selectedItem={file}
              itemToString={(item) => (item ? repoSymbol(service, symbolName(item)) : "")}
              onChange={({ selectedItem }) => setQuery({ file: selectedItem ?? "" })}
            />
          ) : (
            <Dropdown
              id="impact-contract"
              titleText="Contract"
              label="Choose a contract"
              items={contracts}
              itemToString={(item) => item?.label ?? ""}
              selectedItem={contracts.find((item) => item.id === contract) ?? null}
              onChange={({ selectedItem }) => {
                setContract(selectedItem?.id ?? "");
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
                onChange={({ selectedItem }) => selectedItem && setEvidence(selectedItem)}
              />
            </Column>
            <Column sm={4} md={4} lg={5}>
              <DatePicker
                datePickerType="single"
                onChange={(dates) => {
                  const date = dates?.[0];
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
                items={["none", "operational"]}
                itemToString={(item) => (item === "operational" ? "Simulate an operational failure" : "None")}
                selectedItem={scenario}
                onChange={({ selectedItem }) => setScenario(selectedItem ?? "none")}
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
        { key: "service_id", header: "Service" },
        { key: "origin", header: "Evidence", render: (row) => <StatusTag status={originKind(row.evidence_origin)} label={originLabel(row.evidence_origin)} /> },
        { key: "score", header: <ScoreHelp /> },
      ]}
      rows={result.candidates.map((item, index) => ({ ...item, id: `${item.service_id}-${item.file_path}-${index}`, rank: index + 1, code: repoSymbol(item.service_id, symbolName(item.name || item.file_path)), score: item.hybrid_score.toFixed(3) }))}
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
        <p>{(row.structural.impacted_symbols ?? []).map((item) => repoSymbol(row.service_id, symbolName(item.name))).join(", ") || repoSymbol(row.service_id, symbolName(row.name))}</p>
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
