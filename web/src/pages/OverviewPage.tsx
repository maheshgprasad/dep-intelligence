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
import { clusterRelationships, recentReviews, type AppState } from "../api";
import { NamiEmptyState } from "../components/NamiEmptyState";
import { GenerateReportButton } from "../components/GenerateReportButton";
import { PageHeader } from "../components/PageHeader";
import { StatusTag } from "../components/StatusTag";
import { intelligenceView } from "../intelligence";

export function OverviewPage({
  snapshot,
  error,
  loaded,
  refreshing,
  onRefresh,
}: {
  snapshot: AppState | null;
  error: string;
  loaded: boolean;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  const view = intelligenceView(snapshot, refreshing);
  const services = snapshot?.cluster?.services ?? [];
  const [relations, setRelations] = useState<{ from: string; contract: string; to: string }[]>([]);
  const [relationTotal, setRelationTotal] = useState<number | null>(null);
  const [blockedReview, setBlockedReview] = useState("");

  useEffect(() => {
    void recentReviews()
      .then((reviews) => {
        const blocked = reviews.find((item) => item.decision === "block");
        setBlockedReview(blocked ? blocked.review_id : "");
      })
      .catch(() => setBlockedReview(""));
  }, [snapshot?.cluster?.version]);

  useEffect(() => {
    if (!snapshot?.cluster?.version) return;
    void clusterRelationships(6)
      .then((payload) => {
        setRelationTotal(payload.total);
        setRelations(payload.relationships.map((item) => ({
          from: item.from_service,
          contract: item.contract,
          to: item.to_service,
        })));
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
        description="Nami Trace helps teams understand what a change may affect, why, what should be verified, and whether it is ready to proceed."
        status={refreshing ? <p className="helper">Showing the previous snapshot while intelligence refreshes.</p> : null}
        actions={<GenerateReportButton scope="cluster" staleCount={snapshot.cluster?.stale_services?.length || 0} />}
      />
      {snapshot.cluster?.stale_services?.length ? (
        <InlineNotification kind="info" title="Intelligence is out of date" subtitle={`${snapshot.cluster.stale_services.length} service${snapshot.cluster.stale_services.length === 1 ? "" : "s"} ${snapshot.cluster.stale_services.length === 1 ? "has" : "have"} stale intelligence. Refresh before relying on structural paths.`} lowContrast hideCloseButton />
      ) : null}
      {blockedReview ? (
        <InlineNotification kind="warning" title="A recent review needs action" subtitle="A stored review is blocked. Open it to see the evidence and the required next step." lowContrast hideCloseButton />
      ) : null}
      {blockedReview ? <Link href={`/review/${blockedReview}`}>Open the blocked review</Link> : null}
      <Grid>
        <Column sm={4} md={2} lg={4}>
          <Tile>
            <p className="stat-label">Services</p>
            <p className="stat-value">{services.length}</p>
          </Tile>
        </Column>
        <Column sm={4} md={2} lg={4}>
          <Tile>
            <p className="stat-label">Resolved relationships</p>
            <p className="stat-value">{relationTotal ?? "—"}</p>
            <p className="helper">Contracts: {snapshot.cluster?.contract_count ?? 0}. Unresolved references: {snapshot.cluster?.unresolved_count ?? 0}.</p>
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
        <Column sm={4} md={4} lg={6}>
          <ClickableTile href="/review" className="action-tile">
            <h2>Review a change</h2>
            <p>Analyze a PR, commit, branch range, or working tree.</p>
            <ArrowRight />
          </ClickableTile>
        </Column>
        <Column sm={4} md={4} lg={5}>
          <ClickableTile href="/impact" className="action-tile">
            <h2>Explore impact</h2>
            <p>Ask what could be affected if a file or contract changes.</p>
            <ArrowRight />
          </ClickableTile>
        </Column>
        <Column sm={4} md={4} lg={5}>
          <ClickableTile href="/reports" className="action-tile">
            <h2>Generate report</h2>
            <p>Create a point-in-time workspace intelligence report.</p>
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
