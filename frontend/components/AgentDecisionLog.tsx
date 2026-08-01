"use client";

import { useMemo, useState } from "react";

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
  /** Test/demo seam for rendering without a live rank call. */
  initialDecisions?: RankDecision[] | null;
  initialLoading?: boolean;
  initialError?: string | null;
};

const DECISION_STYLES: Record<
  AgentDecision,
  { label: string; className: string; barClassName: string }
> = {
  auto_renew: {
    label: "Auto renew (recommendation)",
    className: "border-ok/25 bg-ok-soft text-ok",
    barClassName: "bg-ok",
  },
  flag_for_review: {
    label: "Flag for review",
    className: "border-warn/25 bg-warn-soft text-warn",
    barClassName: "bg-warn",
  },
  ignore: {
    label: "Ignore",
    className: "border-line bg-[#f1f4f8] text-ink-muted",
    barClassName: "bg-ink-faint",
  },
};

function domainLabel(
  domainId: number,
  byId: Map<number, string>,
): string {
  return byId.get(domainId) ?? `Domain #${domainId}`;
}

export default function AgentDecisionLog({
  domains,
  apiBaseUrl,
  initialDecisions = null,
  initialLoading = false,
  initialError = null,
}: AgentDecisionLogProps) {
  const [decisions, setDecisions] = useState<RankDecision[] | null>(initialDecisions);
  const [loading, setLoading] = useState(initialLoading);
  const [error, setError] = useState<string | null>(initialError);

  const byId = useMemo(() => {
    const map = new Map<number, string>();
    for (const item of domains) {
      map.set(item.id, item.domain);
    }
    return map;
  }, [domains]);

  const sortedDecisions = useMemo(() => {
    if (!decisions) return [];
    return [...decisions].sort((left, right) => {
      if (right.criticality_score !== left.criticality_score) {
        return right.criticality_score - left.criticality_score;
      }
      return left.domain_id - right.domain_id;
    });
  }, [decisions]);

  async function onRank(): Promise<void> {
    if (loading) return;
    if (domains.length === 0) {
      setError("Scan at least one domain before ranking.");
      setDecisions(null);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const next = await rankDomains(
        domains.map((item) => item.id),
        apiBaseUrl,
      );
      setDecisions(next);
    } catch (err) {
      setDecisions(null);
      setError(err instanceof Error ? err.message : "Ranking failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section
      aria-labelledby="agent-decision-log-heading"
      className="aegis-panel space-y-5 rounded-xl p-6 sm:p-7"
      data-state={loading ? "loading" : error ? "error" : decisions ? "populated" : "idle"}
    >
      <div className="space-y-2 border-b border-line pb-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 id="agent-decision-log-heading" className="text-base font-semibold text-ink">
              Agent recommendations
            </h2>
            <p className="mt-1 max-w-2xl text-sm leading-relaxed text-ink-muted">
              Ranking suggests urgency and next steps. It never charges a mandate, mints a
              network token, or completes checkout.
            </p>
          </div>
          <button
            type="button"
            className="aegis-btn aegis-btn-secondary"
            onClick={() => void onRank()}
            disabled={loading || domains.length === 0}
          >
            {loading ? "Ranking…" : "Run ranking"}
          </button>
        </div>
        <p className="rounded-md border border-line bg-[#f8fafb] px-3 py-2 text-xs text-ink-muted">
          <span className="font-medium text-ink">Not a payment.</span>{" "}
          <code className="font-mono text-[11px]">POST /api/agent/rank</code> is advisory and
          policy-gated. Execution stays on a separate payments path.
        </p>
      </div>

      {loading ? (
        <div aria-busy="true" role="status" className="py-6 text-center">
          <p className="text-sm font-medium text-ink">Ranking domains…</p>
          <p className="mt-1 text-sm text-ink-muted">
            Model recommendations are checked against mandate coverage before display.
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

      {!loading && !error && decisions === null ? (
        <p className="text-sm text-ink-muted">
          {domains.length === 0
            ? "Scan domains first, then run ranking to see recommendations."
            : "Run ranking to generate a recommendation for each monitored domain."}
        </p>
      ) : null}

      {!loading && !error && sortedDecisions.length > 0 ? (
        <ul className="space-y-4">
          {sortedDecisions.map((item) => {
            const style = DECISION_STYLES[item.decision];
            return (
              <li
                key={`${item.domain_id}-${item.decision}-${item.criticality_score}`}
                className="rounded-lg border border-line bg-bg-elevated p-4"
                data-decision={item.decision}
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

                <p className="mt-4 whitespace-pre-wrap text-sm leading-relaxed text-ink">
                  {item.reason}
                </p>
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
