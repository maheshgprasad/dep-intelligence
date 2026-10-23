import { useEffect, useState } from "react";
import { ArrowRight } from "@carbon/icons-react";
import {
  ClickableTile,
  Column,
  Grid,
  InlineNotification,
  Link,
  SkeletonText,
  StructuredListBody,
  StructuredListCell,
  StructuredListHead,
  StructuredListRow,
  StructuredListWrapper,
  Tile,
} from "@carbon/react";
import { clusterGraph, type Snapshot } from "../api";
import { NamiEmptyState } from "../components/NamiEmptyState";
import { PageHeader } from "../components/PageHeader";
import { StatusTag } from "../components/StatusTag";
import { intelligenceView } from "../intelligence";
import { isFilePath, symbolName } from "../labels";

export function OverviewPage({
  snapshot,
  error,
  loaded,
  refreshing,
  onRefresh,
}: {
  snapshot: Snapshot | null;
  error: string;
  loaded: boolean;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  const view = intelligenceView(snapshot, refreshing);
  const services = snapshot?.cluster?.services ?? [];
  const [relations, setRelations] = useState<{ from: string; contract: string; to: string }[]>([]);

  useEffect(() => {
    if (!snapshot?.cluster?.version) return;
    void clusterGraph({ limit: 400 })
      .then((payload) => {
        const nodes = new Map(payload.page.nodes.map((node) => [node.key, node]));
        const rows = (payload.page.edges ?? [])
          .filter((edge) => edge.relation === "CONSUMES_HTTP" || edge.relation === "CONSUMES_EVENT" || edge.relation === "HANDLED_BY")
          .slice(0, 6)
          .map((edge) => {
            const source = nodes.get(edge.source);
            const target = nodes.get(edge.target);
            return {
              from: source?.service_id || "service",
              contract: contractLabel(source?.name || source?.qualified_name || edge.relation),
              to: target?.service_id || target?.name || "service",
            };
          });
        setRelations(rows);
      })
      .catch(() => setRelations([]));
  }, [snapshot?.cluster?.version]);

  if (!loaded) return <SkeletonText heading width="40%" />;
  if (error) return <InlineNotification kind="error" title="Nami Trace is unavailable" subtitle={error} lowContrast hideCloseButton />;
  if (!snapshot?.cluster?.version) {
    return (
      <NamiEmptyState
        title="Build Nami's intelligence snapshot"
        description="Nami needs repository graphs and commit history before it can trace cross-repository impact."
        primaryAction={{ label: "Refresh intelligence", onClick: onRefresh }}
        secondaryAction={{ label: "How reviews work", href: "/review" }}
      />
    );
  }

  return (
    <div className="stack-gap">
      <PageHeader
        title="Nami Trace"
        description="Cross-repository change intelligence"
        status={refreshing ? <p className="helper">Showing the previous snapshot while intelligence refreshes.</p> : null}
      />
      <Grid>
        <Column sm={4} md={2} lg={4}>
          <Tile>
            <p className="stat-label">Services</p>
            <p className="stat-value">{services.length}</p>
          </Tile>
        </Column>
        <Column sm={4} md={2} lg={4}>
          <Tile>
            <p className="stat-label">Cross-service contracts</p>
            <p className="stat-value">{snapshot.cluster?.contract_count ?? 0}</p>
          </Tile>
        </Column>
        <Column sm={4} md={2} lg={4}>
          <Tile>
            <p className="stat-label">Intelligence</p>
            <p className="stat-value"><StatusTag status={view.overall} /></p>
          </Tile>
        </Column>
        <Column sm={4} md={2} lg={4}>
          <Tile>
            <p className="stat-label">Needs attention</p>
            <p className="stat-value">{view.attention.length}</p>
          </Tile>
        </Column>
      </Grid>
      <Grid>
        <Column sm={4} md={4} lg={8}>
          <ClickableTile href="/review" className="action-tile">
            <h2>Review a change</h2>
            <p>Analyze a PR, commit, branch range, or working tree.</p>
            <ArrowRight />
          </ClickableTile>
        </Column>
        <Column sm={4} md={4} lg={8}>
          <ClickableTile href="/impact" className="action-tile">
            <h2>Explore impact</h2>
            <p>Ask what could be affected if a file or contract changes.</p>
            <ArrowRight />
          </ClickableTile>
        </Column>
      </Grid>
      <section>
        <h2 className="section-title">Services</h2>
        <StructuredListWrapper>
          <StructuredListHead>
            <StructuredListRow head>
              <StructuredListCell head>Service</StructuredListCell>
              <StructuredListCell head>Status</StructuredListCell>
              <StructuredListCell head>Symbols</StructuredListCell>
            </StructuredListRow>
          </StructuredListHead>
          <StructuredListBody>
            {services.map((service) => (
              <StructuredListRow key={service.id}>
                <StructuredListCell>
                  <Link href={`/architecture?service=${encodeURIComponent(service.id)}`}>{service.id}</Link>
                </StructuredListCell>
                <StructuredListCell>
                  <StatusTag status={service.error ? "unavailable" : snapshot.cluster?.stale_services?.includes(service.id) ? "stale" : service.quality === "ok" ? "current" : "partial"} />
                </StructuredListCell>
                <StructuredListCell>{service.error ? "—" : service.symbols.toLocaleString()}</StructuredListCell>
              </StructuredListRow>
            ))}
          </StructuredListBody>
        </StructuredListWrapper>
      </section>
      <section>
        <h2 className="section-title">Cross-service relationships</h2>
        {relations.length === 0 ? (
          <p className="helper">No cross-service contracts are bound yet.</p>
        ) : (
          <ul className="relation-list">
            {relations.map((item) => (
              <li key={`${item.from}-${item.contract}-${item.to}`}>
                <strong>{item.from}</strong>
                <span>{item.contract}</span>
                <span>→ {item.to}</span>
              </li>
            ))}
          </ul>
        )}
        <Link href="/architecture">View architecture →</Link>
      </section>
      {view.attention.length ? (
        <InlineNotification
          kind="warning"
          title="Needs attention"
          subtitle={view.attention.slice(0, 4).join(". ")}
          lowContrast
          hideCloseButton
        />
      ) : null}
    </div>
  );
}

function contractLabel(value: string): string {
  return isFilePath(value) ? symbolName(value) : value;
}
