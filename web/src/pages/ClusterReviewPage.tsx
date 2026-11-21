import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Button, InlineLoading, InlineNotification, ProgressBar } from "@carbon/react";
import { ClusterExportMenu } from "../components/ExportMenu";
import { cancelClusterReview, getClusterReview } from "../api";
import {
  applyClusterSnapshot,
  decisionText,
  repositoryDetail,
  repositoryStatusText,
  stageState,
  stageStateText,
  validationText,
  type ClusterInterdependency,
  type ClusterRepositoryState,
  type ClusterReviewState,
} from "../clusterReview";
import { NamiDataTable } from "../components/NamiDataTable";
import { PageHeader } from "../components/PageHeader";
import { StatusTag } from "../components/StatusTag";

export function ClusterReviewPage() {
  const { clusterReviewId = "" } = useParams();
  const [review, setReview] = useState<ClusterReviewState | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!clusterReviewId) return;
    let closed = false;
    let timer = 0;
    const pull = () => {
      void getClusterReview(clusterReviewId)
        .then((state) => {
          if (!closed) setReview((current) => applyClusterSnapshot(current, state));
        })
        .catch((exc: unknown) => {
          if (!closed) setError(exc instanceof Error ? exc.message : "The cluster review could not be loaded.");
        });
    };
    pull();
    const source = typeof EventSource === "undefined" ? null : new EventSource("/api/events");
    const onProgress = (event: Event) => {
      const data = JSON.parse((event as MessageEvent).data) as ClusterReviewState;
      if (data.cluster_review_id !== clusterReviewId) return;
      setReview((current) => applyClusterSnapshot(current, data));
    };
    source?.addEventListener("cluster_review_progress", onProgress);
    source?.addEventListener("cluster_review_completed", onProgress);
    source?.addEventListener("cluster_review_cancelled", onProgress);
    source?.addEventListener("resync", () => pull());
    timer = window.setInterval(() => {
      setReview((current) => {
        if (current && ["completed", "incomplete", "cancelled"].includes(current.status)) window.clearInterval(timer);
        return current;
      });
      pull();
    }, 4000);
    return () => {
      closed = true;
      source?.close();
      window.clearInterval(timer);
    };
  }, [clusterReviewId]);

  if (error && !review) {
    return <InlineNotification kind="error" title="Cluster review unavailable" subtitle={error} lowContrast hideCloseButton />;
  }
  if (!review) return <InlineLoading description="Loading cluster review" />;
  return (
    <ClusterReviewProgress
      review={review}
      onCancel={review.status === "running" ? () => {
        void cancelClusterReview(clusterReviewId).then((state) => setReview((current) => applyClusterSnapshot(current, state))).catch((exc: unknown) => {
          setError(exc instanceof Error ? exc.message : "The cluster review could not be cancelled.");
        });
      } : undefined}
    />
  );
}

export function ClusterReviewProgress({ review, onCancel }: { review: ClusterReviewState; onCancel?: () => void }) {
  const finished = review.status === "completed" || review.status === "incomplete" || review.status === "cancelled";
  const label = review.cluster_label || review.cluster_id || "cluster";
  const counters = review.counters;
  const running = review.running?.length ? review.running : review.repositories.filter((item) => item.status === "inspecting" || item.status === "reviewing");
  const title = review.status === "cancelled"
    ? `${label} cluster review cancelled`
    : review.status === "incomplete"
      ? `${label} cluster review incomplete`
      : finished
        ? `${label} cluster review complete`
        : `Reviewing ${label} cluster`;
  const changed = review.repositories.filter((item) => item.change_detected && item.status !== "no_change");

  return (
    <div className="stack-gap">
      <PageHeader
        title={title}
        eyebrow="Cluster change review"
        breadcrumbs={[{ label: "Review change", href: "/review" }, { label: label }]}
        actions={onCancel ? <Button kind="danger--tertiary" onClick={onCancel}>Cancel cluster review</Button> : null}
      />
      {review.cancel_requested && review.status === "running" ? (
        <InlineNotification kind="info" title="Cancellation requested" subtitle="Finished repositories stay finished. The current repository stops when its stage can stop." lowContrast hideCloseButton />
      ) : null}
      <ProgressBar
        label="Repositories inspected"
        value={review.percent}
        max={100}
        status={review.status === "cancelled" || counters.reviews_failed > 0 ? "error" : finished ? "finished" : "active"}
      />
      <div className="cluster-progress-summary">
        <p>{counters.repositories_inspected} / {counters.repository_total} repositories inspected</p>
        <p>Changes detected {counters.repositories_with_changes}</p>
        <p>No changes {counters.repositories_no_change}</p>
        <p>Reviews completed {counters.reviews_completed}</p>
        <p>Currently running {counters.repositories_running}</p>
        {counters.reviews_failed > 0 ? <p>Failed {counters.reviews_failed}</p> : null}
        {review.status === "running" && review.analysis_label ? <p>{review.analysis_label}</p> : null}
      </div>
      {running.length > 0 ? (
        <section aria-label="Currently analysing">
          <h2 className="section-title">{running.length === 1 ? "Currently analysing" : `Currently analysing ${running.length} repositories`}</h2>
          {running.map((item) => (
            <div key={item.service_id}>
              <p>{repositoryName(item)}</p>
              <p className="helper">{repositoryDetail(item)}</p>
              {item.status === "reviewing" ? <StageList review={review} repository={item} /> : null}
            </div>
          ))}
        </section>
      ) : null}
      {finished && review.assessment ? (
        <section aria-label="Cluster assessment">
          <h2 className="section-title">Repository outcomes</h2>
          {changed.length === 0 ? <p>No repository contained a change.</p> : changed.map((item) => (
            <p key={item.service_id}>
              {item.status === "failed" ? "Failed" : decisionText(item.decision)}
              {" "}
              {repositoryName(item)}
              {item.review_id ? <> · <Link to={`/review/${item.review_id}`}>View repository review</Link></> : null}
              {item.validation_status ? ` · ${validationText(item.validation_status)}` : null}
              {item.error_summary ? ` · ${item.error_summary}` : null}
            </p>
          ))}
          <h2 className="section-title">Cluster-wide impact</h2>
          <p>{review.assessment.evidenced_service_count} / {review.repository_total} services have evidenced impact</p>
          <h2 className="section-title">Cluster assessment</h2>
          <p>{review.assessment.label}</p>
          {review.assessment.summary ? <p>{review.assessment.summary}</p> : null}
          <div className="button-row">
            <Link to={`/review/cluster/${review.cluster_review_id}/detail`}>View cluster review</Link>
          </div>
        </section>
      ) : null}
      <section aria-label="Repository progress">
        <h2 className="section-title">Repository progress</h2>
        <ul className="cluster-progress-list">
          {review.repositories.map((item) => (
            <li key={item.service_id}>
              <span aria-hidden="true">{marker(item.status)} </span>
              <span>{repositoryName(item)}</span>
              {item.status === "completed" && item.decision ? <StatusTag status={item.decision} label={decisionText(item.decision)} /> : null}
              {item.status === "failed" ? <StatusTag status="failed" label="Failed" /> : null}
              <p className="helper">{repositoryDetail(item)}</p>
              {item.review_id ? <Link to={`/review/${item.review_id}`}>View repository review</Link> : null}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

export function ClusterReviewDetailPage() {
  const { clusterReviewId = "" } = useParams();
  const [review, setReview] = useState<ClusterReviewState | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!clusterReviewId) return;
    void getClusterReview(clusterReviewId)
      .then(setReview)
      .catch((exc: unknown) => setError(exc instanceof Error ? exc.message : "The cluster review could not be loaded."));
  }, [clusterReviewId]);

  if (error && !review) {
    return <InlineNotification kind="error" title="Cluster review unavailable" subtitle={error} lowContrast hideCloseButton />;
  }
  if (!review) return <InlineLoading description="Loading cluster review" />;
  return <ClusterReviewRecord review={review} />;
}

export function ClusterReviewRecord({ review }: { review: ClusterReviewState }) {
  const label = review.cluster_label || review.cluster_id || "Cluster";
  const assessment = review.assessment;
  const evidenced = assessment?.evidenced_services ?? [];
  return (
    <div className="stack-gap">
      <PageHeader
        title={`${label} cluster review`}
        eyebrow="Stored cluster review"
        breadcrumbs={[
          { label: "Review change", href: "/review" },
          { label, href: `/review/cluster/${review.cluster_review_id}` },
          { label: "Review" },
        ]}
        actions={<ClusterExportMenu clusterReviewId={review.cluster_review_id} summary={review.copy_summary || assessment?.summary || label} />}
      />
      <section aria-label="Cluster assessment">
        <h2 className="section-title">Cluster assessment</h2>
        <p>{assessment?.label || "Incomplete"}</p>
        {assessment?.summary ? <p>{assessment.summary}</p> : null}
        {review.source?.since || review.source?.until ? (
          <p>Timeframe {review.source?.since || ""} → {review.source?.until || ""}</p>
        ) : null}
        <p className="helper">
          {review.counters.repositories_inspected} repositories inspected
          {" · "}
          {review.counters.repositories_with_changes} contained changes
          {" · "}
          {review.counters.repositories_no_change} had no changes
          {commitTotal(review) ? ` · ${commitTotal(review)} commits analysed` : ""}
        </p>
        {review.interdependencies?.diagnostic?.message ? <p>{review.interdependencies.diagnostic.message}</p> : null}
      </section>
      <Attention review={review} />
      <InterdependencyChanges review={review} />
      <section aria-label="Cluster-wide impact">
        <h2 className="section-title">Cluster-wide impact</h2>
        <p>{assessment?.evidenced_service_count ?? evidenced.length} / {review.repository_total} services have evidenced impact</p>
        <p>Changed services {(review.interdependencies?.changed_services || []).length}</p>
        <p>Unchanged but affected {(review.interdependencies?.unchanged_but_affected_services || []).length}</p>
        {evidenced.length > 0 ? <p>{evidenced.join(", ")}</p> : <p>No service has evidenced impact.</p>}
      </section>
        {review.repositories.some((item) => (item.commits?.length ?? 0) > 0) ? (
          <section aria-label="Commits in timeframe">
            <h2 className="section-title">Commits in timeframe</h2>
            {review.repositories.filter((item) => (item.commits?.length ?? 0) > 0).map((item) => (
              <div key={item.service_id}>
                <p>{repositoryName(item)}{item.commit_count ? ` · ${item.commit_count} commit${item.commit_count === 1 ? "" : "s"}` : ""}</p>
                {(item.commits ?? []).map((commit) => (
                  <p key={commit.sha} className="helper">{commit.committed_at.slice(0, 10)} · {commit.sha.slice(0, 7)} · {commit.subject}</p>
                ))}
              </div>
            ))}
          </section>
        ) : null}
      <NamiDataTable
        title="Repository reviews"
        columns={[
          { key: "service", header: "Repository" },
          { key: "result", header: "Result", render: (row) => row.resultTag ? <StatusTag status={row.resultTag} label={row.result} /> : row.result },
          { key: "validation", header: "Validation" },
          { key: "review", header: "Boundary Gate review", render: (row) => row.review_id ? <Link to={`/review/${row.review_id}`}>View repository review</Link> : row.reviewNote },
        ]}
        rows={review.repositories.map((item) => ({
          id: item.service_id || item.repository,
          service: repositoryName(item),
          result: repositoryStatusText(item),
          resultTag: item.status === "completed" ? item.decision : item.status === "failed" ? "failed" : "",
          validation: item.validation_status ? validationText(item.validation_status) : "",
          review_id: item.review_id,
          reviewNote: item.status === "no_change" ? "No review" : item.error_summary || "",
        }))}
        searchable
        paginate
        emptyTitle="No repositories"
        emptyDescription="This cluster review has no repository results."
      />
    </div>
  );
}

const COUNT_ROWS: { key: string; label: string }[] = [
  { key: "examined", label: "Relationships examined" },
  { key: "broken", label: "Broken" },
  { key: "potentially_broken", label: "Potentially breaking" },
  { key: "modified", label: "Modified" },
  { key: "introduced", label: "Introduced" },
  { key: "relationship_removed", label: "Removed" },
  { key: "coordinated_change", label: "Coordinated" },
  { key: "resolved_by_change_set", label: "Resolved" },
  { key: "unchanged", label: "Unchanged" },
];

const RESULT_LABEL: Record<string, string> = {
  broken: "Broken",
  potentially_broken: "Potentially breaking",
  modified: "Modified",
  introduced: "Introduced",
  relationship_removed: "Removed",
  coordinated_change: "Coordinated change",
  resolved_by_change_set: "Resolved",
};

function Attention({ review }: { review: ClusterReviewState }) {
  const actions = (review.interdependencies?.items || [])
    .map((item) => item.required_action)
    .filter((item): item is string => Boolean(item));
  const unique = [...new Set(actions)];
  if (!review.interdependencies) return null;
  return (
    <section aria-label="What needs attention">
      <h2 className="section-title">What needs attention</h2>
      {unique.length ? unique.map((item) => <p key={item}>{item}</p>) : <p>Nothing further is required from the relationship comparison.</p>}
    </section>
  );
}

function InterdependencyChanges({ review }: { review: ClusterReviewState }) {
  const [showUnchanged, setShowUnchanged] = useState(false);
  const analysis = review.interdependencies;
  if (!analysis) return null;
  const summary = analysis.summary || {};
  const changed = (analysis.items || []).filter((item) => item.classification !== "unchanged");
  const unchanged = (analysis.items || []).filter((item) => item.classification === "unchanged");
  const affected = analysis.unchanged_but_affected_services || [];
  return (
    <section aria-label="Interdependency changes">
      <h2 className="section-title">Interdependency changes</h2>
      <p className="helper">{comparisonLabel(analysis.comparison_mode)}</p>
      <Coverage analysis={analysis} />
      <ul className="cluster-progress-list">
        {COUNT_ROWS.map((row) => (
          <li key={row.key}>{row.label} {summary[row.key] ?? 0}</li>
        ))}
      </ul>
      {affected.length > 0 ? (
        <div>
          <h3>Unchanged but affected</h3>
          <p>Unchanged but affected: {affected.join(", ")}</p>
          <p className="helper">No commit or change was detected for these repositories, but a dependency they use changed.</p>
        </div>
      ) : null}
      {changed.map((item) => <RelationshipCard key={`${item.classification}-${providerOf(item)}-${item.contract_id}-${consumerOf(item)}`} item={item} />)}
      {unchanged.length > 0 ? (
        <div>
          <p>{unchanged.length} {unchanged.length === 1 ? "relationship" : "relationships"} unchanged</p>
          <Button kind="ghost" size="sm" onClick={() => setShowUnchanged((current) => !current)}>
            {showUnchanged ? "Hide unchanged" : "Show unchanged"}
          </Button>
          {showUnchanged ? unchanged.map((item) => (
            <p key={`${providerOf(item)}-${item.contract_id}-${consumerOf(item)}`} className="helper">
              {providerOf(item)} · {item.contract_id} · {consumerOf(item)}
            </p>
          )) : null}
        </div>
      ) : null}
    </section>
  );
}

function RelationshipCard({ item }: { item: ClusterInterdependency }) {
  const provider = providerOf(item);
  const consumer = consumerOf(item);
  const path = (item.path || []).map((step) => step.service_id).filter(Boolean).join(" → ");
  const reviews = item.child_review_ids || [];
  return (
    <article className="cluster-relationship">
      <h3>{RESULT_LABEL[item.classification] || item.classification}</h3>
      <p>Provider {provider}</p>
      <p>Contract {item.old_contract && item.new_contract ? `${item.old_contract} → ${item.new_contract}` : item.contract_id}</p>
      <p>Consumer {consumer}</p>
      {item.before ? <p>Before {presence(item.before)}</p> : null}
      {item.after ? <p>After {presence(item.after)}</p> : null}
      <p>{item.statement}</p>
      {item.migration_status === "incomplete" ? (
        <div>
          <p>Migration incomplete</p>
          {item.migrated_consumers?.length ? <p>Migrated consumers {item.migrated_consumers.join(", ")}</p> : null}
          {item.outstanding_consumers?.length ? <p>Outstanding consumers {item.outstanding_consumers.join(", ")}</p> : null}
        </div>
      ) : null}
      {path ? <p className="helper">{path}</p> : null}
      {item.required_action ? <p>Action {item.required_action}</p> : null}
      {item.evidence_ids?.length ? <p className="helper">Evidence {item.evidence_ids.join(", ")}</p> : null}
      {(item.child_evidence_refs || []).map((ref) => (
        <p key={`${ref.review_id}-${ref.evidence_id}`} className="helper">Review {(ref.review_id || "").slice(0, 8)} · {ref.evidence_id}</p>
      ))}
      {reviews.map((reviewId) => <p key={reviewId}><Link to={`/review/${reviewId}`}>View repository review</Link></p>)}
    </article>
  );
}

function Coverage({ analysis }: { analysis: NonNullable<ClusterReviewState["interdependencies"]> }) {
  const coverage = analysis.comparison_coverage;
  if (!coverage) return null;
  const partial = coverage.services_partial || [];
  const types = coverage.relationship_types || {};
  return (
    <div>
      {analysis.comparison_mode === "partial" && partial.length ? (
        <p>{coverage.services_exact_before ?? 0} / {coverage.services_total ?? 0} services reconstructed exactly. Partial services: {partial.join(", ")}.</p>
      ) : null}
      {Object.keys(types).length ? <p className="helper">{Object.entries(types).map(([kind, value]) => `${kind} ${value}`).join(" · ")}</p> : null}
      {(analysis.limitations || []).map((item) => <p key={item} className="helper">{item}</p>)}
    </div>
  );
}

function providerOf(item: ClusterInterdependency): string {
  return item.provider_service || item.source_service;
}

function consumerOf(item: ClusterInterdependency): string {
  return item.consumer_service || item.target_service;
}

function presence(side: { provider_present?: boolean; consumer_reference_present?: boolean }): string {
  const provider = side.provider_present ? "Provider present" : "Provider absent";
  const consumer = side.consumer_reference_present ? "Consumer reference present" : "Consumer reference absent";
  return `${provider} · ${consumer}`;
}

function comparisonLabel(mode: string | undefined): string {
  if (mode === "exact_boundary_snapshots") return "Exact boundary dependency comparison";
  if (mode === "partial") return "Partial boundary comparison";
  return "Evidence-derived change-set comparison";
}

function commitTotal(review: ClusterReviewState): number {
  return review.repositories.reduce((sum, item) => sum + (item.commit_count || 0), 0);
}

function StageList({ review, repository }: { review: ClusterReviewState; repository: ClusterRepositoryState }) {
  const stages = review.review_stages ?? [];
  return (
    <ol className="cluster-stage-list" aria-label={`${repositoryName(repository)} review stage`}>
      {stages.map((stage) => {
        const state = stageState(stages, repository.stage, stage.stage);
        return (
          <li key={stage.stage}>
            <span aria-hidden="true">{state === "complete" ? "✓" : state === "current" ? "▶" : "○"} </span>
            {stage.label} {stageStateText(state)}
          </li>
        );
      })}
    </ol>
  );
}

function repositoryName(item: { service_id: string; repository: string }): string {
  return item.service_id || item.repository;
}

function marker(status: string): string {
  if (status === "pending") return "○";
  if (status === "inspecting" || status === "reviewing") return "▶";
  if (status === "failed") return "!";
  if (status === "cancelled") return "–";
  return "✓";
}
