"use client";

import { useEffect, useMemo, useState } from "react";

import {
  rankDomains,
  type AgentDecision,
  type RankDecision,
} from "@/lib/aegisApi";

export type DecisionDomainOption = {
  id: number;
  domain: string;
};

export type AgentDecisionLogProps = {
  domains: DecisionDomainOption[];
  apiBaseUrl?: string;
  selectedDomainId?: number | null;
  onDecisionsChange?: (decisions: RankDecision[] | null) => void;
  /** Test/demo seam for rendering without a live rank call. */
  initialDecisions?: RankDecision[] | null;
  initialLoading?: boolean;
  initialError?: string | null;
  /** Domain IDs the initialDecisions were produced for (staleness tests). */
  initialRankedDomainIds?: number[];
};

const DECISION_STYLES: Record<
  AgentDecision,
  { label: string; className: string; barClassName: string; hint: string }
> = {
  auto_renew: {
    label: "Auto renew · autonomous when mandated",
    className: "border-ok/25 bg-ok-soft text-ok",
    barClassName: "bg-ok",
    hint: "OpenAI + policy approved charging under an active matching mandate.",
  },
  flag_for_review: {
    label: "Flag for review · no autonomous charge",
    className: "border-warn/25 bg-warn-soft text-warn",
    barClassName: "bg-warn",
    hint: "Inventory may look calm; the model still wants a human before payment.",
  },
  ignore: {
    label: "Ignore · no charge",
    className: "border-line bg-neutral-soft text-ink-muted",
    barClassName: "bg-ink-faint",
    hint: "No renewal action recommended right now.",
  },
};

function domainIdsKey(ids: number[]): string {
  return [...ids].sort((left, right) => left - right).join(",");
}

function domainLabel(
  domainId: number,
  byId: Map<number, string>,
): string {
  return byId.get(domainId) ?? `Domain #${domainId}`;
}

export default function AgentDecisionLog({
  domains,
  apiBaseUrl,
  selectedDomainId = null,
  onDecisionsChange,
  initialDecisions = null,
  initialLoading = false,
  initialError = null,
  initialRankedDomainIds,
}: AgentDecisionLogProps) {
  const [decisions, setDecisions] = useState<RankDecision[] | null>(initialDecisions);
  const [loading, setLoading] = useState(initialLoading);
  const [error, setError] = useState<string | null>(initialError);
  const [rankedIdsKey, setRankedIdsKey] = useState<string | null>(() => {
    if (initialDecisions === null) return null;
    if (initialRankedDomainIds) return domainIdsKey(initialRankedDomainIds);
    return domainIdsKey(domains.map((item) => item.id));
  });

  useEffect(() => {
    onDecisionsChange?.(decisions);
  }, [decisions, onDecisionsChange]);

  const byId = useMemo(() => {
    const map = new Map<number, string>();
    for (const item of domains) {
      map.set(item.id, item.domain);
    }
    return map;
  }, [domains]);

  const currentIdsKey = useMemo(
    () => domainIdsKey(domains.map((item) => item.id)),
    [domains],
  );

  const sortedDecisions = useMemo(() => {
    if (!decisions) return [];
    return [...decisions].sort((left, right) => {
      if (right.criticality_score !== left.criticality_score) {
        return right.criticality_score - left.criticality_score;
      }
      return left.domain_id - right.domain_id;
    });
  }, [decisions]);

  const isStale =
    decisions !== null && rankedIdsKey !== null && rankedIdsKey !== currentIdsKey;

  const panelState = loading
    ? "loading"
    : error
      ? "error"
      : decisions === null
        ? "idle"
        : sortedDecisions.length === 0
          ? "empty"
          : "populated";

  async function onRank(): Promise<void> {
    if (loading) return;
    if (domains.length === 0) {
      setError("Scan at least one domain before ranking.");
      setDecisions(null);
      setRankedIdsKey(null);
      return;
    }

    const requestIds = domains.map((item) => item.id);
    setLoading(true);
    setError(null);
    try {
      const next = await rankDomains(requestIds, apiBaseUrl);
      setDecisions(next);
      setRankedIdsKey(domainIdsKey(requestIds));
    } catch (err) {
      setDecisions(null);
      setRankedIdsKey(null);
      setError(err instanceof Error ? err.message : "Ranking failed");
    } finally {
      setLoading(false);
    }
  }

  const showEmptyGuidance =
    !loading && !error && (decisions === null || sortedDecisions.length === 0);

  return (
    <section
      aria-labelledby="agent-decision-log-heading"
      className="aegis-panel space-y-5 rounded-xl p-6 sm:p-7"
      data-state={panelState}
    >
      <div className="space-y-2 border-b border-line pb-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
              Step 3 · OpenAI
            </p>
            <h2 id="agent-decision-log-heading" className="mt-1 text-base font-semibold text-ink">
              Agent recommendations
            </h2>
            <p className="mt-1 max-w-2xl text-sm leading-relaxed text-ink-muted">
              <span className="font-medium text-ink">gpt-4o</span> scores urgency from live
              scan fields, then deterministic policy keeps or downgrades{" "}
              <code className="font-mono text-[11px]">auto_renew</code>. Ranking never charges.
            </p>
          </div>
          <button
            type="button"
            className="aegis-btn aegis-btn-secondary"
            onClick={() => void onRank()}
            disabled={loading || domains.length === 0}
          >
            {loading ? "Calling OpenAI…" : "Run OpenAI ranking"}
          </button>
        </div>
        <p className="rounded-md border border-line bg-neutral-soft px-3 py-2 text-xs text-ink-muted">
          <span className="font-medium text-ink">Not a payment.</span>{" "}
          <code className="font-mono text-[11px]">POST /api/agent/rank</code> is advisory until an
          active mandate and final <code className="font-mono text-[11px]">auto_renew</code> unlock
          execution.
        </p>
      </div>

      {loading ? (
        <div aria-busy="true" role="status" className="py-6 text-center">
          <p className="text-sm font-medium text-ink">Ranking with OpenAI…</p>
          <p className="mt-1 text-sm text-ink-muted">
            Model output is checked against mandate coverage before display.
          </p>
        </div>
      ) : null}

      {!loading && error ? (
        <p
          role="alert"
          className="rounded-lg border border-danger/20 bg-danger-soft px-3 py-2.5 text-sm text-danger"
        >
          {error}
        </p>
      ) : null}

      {showEmptyGuidance ? (
        <p className="text-sm text-ink-muted">
          {decisions !== null && sortedDecisions.length === 0
            ? "Ranking completed with no recommendations."
            : domains.length === 0
              ? "Scan domains first, then run OpenAI ranking."
              : "Run OpenAI ranking to decide renew, review, or ignore for each domain."}
        </p>
      ) : null}

      {!loading && !error && sortedDecisions.length > 0 ? (
        <>
          {isStale ? (
            <p
              role="status"
              className="rounded-lg border border-warn/20 bg-warn-soft px-3 py-2.5 text-sm text-warn"
            >
              Inventory changed since this ranking. Results may omit newly scanned domains —
              run ranking again to refresh.
            </p>
          ) : null}
          <ul className="space-y-4">
            {sortedDecisions.map((item) => {
              const style = DECISION_STYLES[item.decision];
              const selected = selectedDomainId === item.domain_id;
              return (
                <li
                  key={item.domain_id}
                  className={`rounded-lg border p-4 ${
                    selected
                      ? "border-accent/40 bg-accent-soft/40"
                      : "border-line bg-bg-elevated"
                  }`}
                  data-decision={item.decision}
                  data-selected={selected ? "true" : undefined}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <p className="font-mono text-sm font-medium text-ink">
                        {domainLabel(item.domain_id, byId)}
                      </p>
                      <p className="mt-0.5 text-xs text-ink-faint">Asset {item.domain_id}</p>
                    </div>
                    <span
                      className={`inline-flex items-center rounded-md border px-2.5 py-1 text-[11px] font-semibold tracking-wide ${style.className}`}
                    >
                      {style.label}
                    </span>
                  </div>

                  <div className="mt-4">
                    <div className="flex items-center justify-between gap-2 text-xs text-ink-muted">
                      <span>Criticality</span>
                      <span className="font-mono tabular-nums text-ink">
                        {item.criticality_score}/100
                      </span>
                    </div>
                    <div
                      className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-line"
                      role="meter"
                      aria-label={`Criticality score ${item.criticality_score} out of 100`}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={item.criticality_score}
                    >
                      <div
                        className={`h-full rounded-full ${style.barClassName}`}
                        style={{ width: `${item.criticality_score}%` }}
                      />
                    </div>
                  </div>

                  <p className="mt-3 text-xs text-ink-muted">{style.hint}</p>
                  <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-ink">
                    {item.reason}
                  </p>
                </li>
              );
            })}
          </ul>
        </>
      ) : null}
    </section>
  );
}
