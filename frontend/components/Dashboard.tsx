"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import DomainList from "@/components/DomainList";
import AgentDecisionLog from "@/components/AgentDecisionLog";
import MandateSetup from "@/components/MandateSetup";
import PaymentExecution from "@/components/PaymentExecution";
import {
  fetchDomains,
  isValidScanDomain,
  scanDomain,
  type DomainSummary,
  type ScanResult,
} from "@/lib/aegisApi";
import { summarizeDomains } from "@/lib/domainSummary";

export type DashboardProps = {
  apiBaseUrl?: string;
};

function describePartialScan(result: ScanResult): string {
  const missing: string[] = [];
  if (result.expiry_date === null) missing.push("domain expiry");
  if (result.cert_expiry_date === null) missing.push("certificate expiry");
  if (missing.length === 0) {
    return result.dns_risk_detail
      ? `Scan saved. DNS detail: ${result.dns_risk_detail}`
      : "Scan saved with full detector coverage.";
  }
  const base = `Scan saved with partial results (missing ${missing.join(" and ")}).`;
  return result.dns_risk_detail ? `${base} DNS detail: ${result.dns_risk_detail}` : base;
}

function TelemetryItem({
  label,
  value,
  hint,
}: {
  label: string;
  value: number;
  hint?: string;
}) {
  return (
    <div className="aegis-telemetry-item">
      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
        {label}
      </p>
      <p className="mt-1 font-display text-2xl font-semibold tabular-nums text-ink">{value}</p>
      {hint ? <p className="mt-0.5 text-[11px] text-ink-muted">{hint}</p> : null}
    </div>
  );
}

export default function Dashboard({ apiBaseUrl }: DashboardProps) {
  const [domains, setDomains] = useState<DomainSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [scanInput, setScanInput] = useState("");
  const [scanning, setScanning] = useState(false);
  const [scanMessage, setScanMessage] = useState<string | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);
  const requestIdRef = useRef(0);
  const today = new Date();

  const loadDomains = useCallback(async (): Promise<void> => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoading(true);
    setError(null);
    try {
      const next = await fetchDomains(apiBaseUrl);
      if (requestId !== requestIdRef.current) return;
      setDomains(next);
    } catch (err) {
      if (requestId !== requestIdRef.current) return;
      setDomains([]);
      setError(err instanceof Error ? err.message : "Could not load domains");
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false);
      }
    }
  }, [apiBaseUrl]);

  useEffect(() => {
    // Defer so the shared loader's setState is not synchronous inside the effect.
    const timer = window.setTimeout(() => {
      void loadDomains();
    }, 0);
    return () => {
      window.clearTimeout(timer);
      requestIdRef.current += 1;
    };
  }, [loadDomains]);

  async function onScan(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const domain = scanInput.trim();
    setScanMessage(null);
    setScanError(null);

    if (!isValidScanDomain(domain)) {
      setScanError("Enter a bare hostname like example.com (no URL scheme).");
      return;
    }

    setScanning(true);
    try {
      const result = await scanDomain(domain, apiBaseUrl);
      setScanMessage(describePartialScan(result));
      setScanInput("");
      await loadDomains();
    } catch (err) {
      setScanError(err instanceof Error ? err.message : "Scan failed");
    } finally {
      setScanning(false);
    }
  }

  const summary = summarizeDomains(domains, today);
  const domainOptions = domains.map((item) => ({
    id: item.id,
    domain: item.domain,
  }));
  const showFullSkeleton = loading && domains.length === 0;
  const refreshing = loading && domains.length > 0;

  return (
    <div className="aegis-fade-in px-5 py-8 sm:px-8 sm:py-10">
      <div className="mx-auto flex max-w-6xl flex-col gap-8">
        <header className="aegis-rise flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-ink-faint">
              Operations
            </p>
            <h1 className="mt-1 font-display text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
              Risk inventory
            </h1>
            <p className="mt-2 max-w-lg text-sm text-ink-muted">
              Scans persist to Postgres. Empty fields mean a detector missed data—not an all-clear.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void loadDomains()}
            className="aegis-btn aegis-btn-secondary self-start sm:self-auto"
            disabled={loading || scanning}
          >
            {refreshing ? "Refreshing…" : "Refresh inventory"}
          </button>
        </header>

        <div className="aegis-rise aegis-telemetry" style={{ animationDelay: "60ms" }}>
          <TelemetryItem label="Monitored" value={summary.monitored} />
          <TelemetryItem label="Urgent" value={summary.urgent} hint="≤ 7 days" />
          <TelemetryItem label="Review" value={summary.reviewSoon} hint="≤ 30 days" />
          <TelemetryItem label="DNS flagged" value={summary.dnsRisk} hint="high confidence" />
        </div>

        <div
          className="aegis-rise grid gap-8 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] xl:items-start"
          style={{ animationDelay: "120ms" }}
        >
          <section className="space-y-5">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-ink">Scan &amp; inventory</h2>
              <span className="font-mono text-[10px] text-ink-faint">GET /api/domains</span>
            </div>

            <form
              className="rounded-xl border border-line bg-bg-elevated p-4 shadow-[0_1px_0_rgba(12,18,34,0.04)]"
              onSubmit={(event) => void onScan(event)}
            >
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                <label className="block flex-1 space-y-1.5 text-sm text-ink">
                  <span className="font-medium">Hostname</span>
                  <input
                    className="aegis-input font-mono text-[15px]"
                    placeholder="your-domain.com"
                    value={scanInput}
                    onChange={(event) => setScanInput(event.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    disabled={scanning}
                  />
                </label>
                <button
                  type="submit"
                  disabled={scanning || loading}
                  className="aegis-btn aegis-btn-primary min-w-[7.5rem] sm:mb-px"
                >
                  {scanning ? "Scanning…" : "Run scan"}
                </button>
              </div>
              <p className="mt-3 text-xs text-ink-faint">
                Only scan assets you own or are explicitly authorized to assess.
              </p>
            </form>

            {scanError ? (
              <p role="alert" className="text-sm font-medium text-danger">
                {scanError}
              </p>
            ) : null}
            {scanMessage ? (
              <p
                role="status"
                className="rounded-lg border border-accent/25 bg-accent-soft px-3 py-2.5 text-sm text-accent"
              >
                {scanMessage}
              </p>
            ) : null}

            {refreshing ? (
              <p role="status" className="text-xs font-medium text-ink-muted">
                Refreshing inventory…
              </p>
            ) : null}

            <DomainList
              domains={domains}
              loading={showFullSkeleton}
              error={error}
              today={today}
            />
          </section>

          <aside className="xl:sticky xl:top-6">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-ink">Renewal mandate</h2>
              <span className="rounded bg-copper-soft px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-copper">
                Prava
              </span>
            </div>
            <MandateSetup domains={domainOptions} apiBaseUrl={apiBaseUrl} />
          </aside>
        </div>

        <div className="aegis-rise" style={{ animationDelay: "180ms" }}>
          <AgentDecisionLog domains={domainOptions} apiBaseUrl={apiBaseUrl} />
        </div>

        <div className="aegis-rise" style={{ animationDelay: "220ms" }}>
          <PaymentExecution domains={domainOptions} apiBaseUrl={apiBaseUrl} />
        </div>
      </div>
    </div>
  );
}
