"use client";

import { FormEvent, useEffect, useState } from "react";

import DomainList, { type DomainSummary } from "@/components/DomainList";
import MandateSetup from "@/components/MandateSetup";
import {
  fetchDomains,
  isValidScanDomain,
  scanDomain,
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

function Stat({
  label,
  value,
  tone = "default",
  delay,
}: {
  label: string;
  value: number;
  tone?: "default" | "danger" | "warn" | "dns";
  delay: string;
}) {
  const toneClass =
    tone === "danger"
      ? "border-danger/15 bg-danger-soft"
      : tone === "warn"
        ? "border-warn/15 bg-warn-soft"
        : tone === "dns"
          ? "border-dns/15 bg-dns-soft"
          : "border-line bg-bg-elevated";

  const valueClass =
    tone === "danger"
      ? "text-danger"
      : tone === "warn"
        ? "text-warn"
        : tone === "dns"
          ? "text-dns"
          : "text-ink";

  return (
    <div
      className={`aegis-rise rounded-xl border px-5 py-4 ${toneClass}`}
      style={{ animationDelay: delay }}
    >
      <dt className="text-[11px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
        {label}
      </dt>
      <dd className={`mt-2 font-display text-3xl font-semibold tabular-nums tracking-tight ${valueClass}`}>
        {value}
      </dd>
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
  const today = new Date();

  async function loadDomains(): Promise<void> {
    setLoading(true);
    setError(null);
    try {
      const next = await fetchDomains(apiBaseUrl);
      setDomains(next);
    } catch (err) {
      setDomains([]);
      setError(err instanceof Error ? err.message : "Could not load domains");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;

    void fetchDomains(apiBaseUrl)
      .then((next) => {
        if (cancelled) return;
        setDomains(next);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setDomains([]);
        setError(err instanceof Error ? err.message : "Could not load domains");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [apiBaseUrl]);

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
  const listForMandate = domains.map((item) => ({
    id: item.id,
    domain: item.domain,
  }));

  return (
    <main className="aegis-fade-in min-h-screen px-5 py-8 sm:px-8 sm:py-12">
      <div className="mx-auto flex max-w-6xl flex-col gap-10">
        <header className="aegis-rise flex flex-col gap-6 border-b border-line pb-8 sm:flex-row sm:items-end sm:justify-between">
          <div className="max-w-2xl space-y-4">
            <p className="font-display text-4xl font-semibold tracking-tight text-ink sm:text-5xl">
              Aegis
            </p>
            <div className="space-y-2">
              <h1 className="text-lg font-medium text-ink sm:text-xl">
                Infrastructure renewal under mandate control
              </h1>
              <p className="max-w-xl text-sm leading-relaxed text-ink-muted sm:text-[15px]">
                Monitor domain expiry, TLS certificates, and confirmed DNS takeover risk.
                Renewals execute only when a user-approved Prava mandate covers the merchant
                and amount.
              </p>
            </div>
          </div>
          <div className="shrink-0 rounded-lg border border-line bg-bg-elevated px-3 py-2 text-xs text-ink-muted">
            Live inventory · <span className="font-mono text-ink">/api/domains</span>
          </div>
        </header>

        <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Stat label="Monitored" value={summary.monitored} delay="40ms" />
          <Stat label="Urgent" value={summary.urgent} tone="danger" delay="80ms" />
          <Stat label="Review soon" value={summary.reviewSoon} tone="warn" delay="120ms" />
          <Stat label="DNS risk" value={summary.dnsRisk} tone="dns" delay="160ms" />
        </dl>

        <section className="aegis-rise space-y-5" style={{ animationDelay: "180ms" }}>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-ink">Domain risk inventory</h2>
              <p className="mt-1 max-w-2xl text-sm text-ink-muted">
                Null detector fields are partial results from an unavailable external service—not
                a healthy signal.
              </p>
            </div>
            <button
              type="button"
              onClick={() => void loadDomains()}
              className="aegis-btn aegis-btn-secondary"
              disabled={loading || scanning}
            >
              Refresh
            </button>
          </div>

          <form
            className="aegis-panel flex flex-col gap-3 rounded-xl p-4 sm:flex-row sm:items-end"
            onSubmit={(event) => void onScan(event)}
          >
            <label className="block flex-1 space-y-1.5 text-sm text-ink">
              <span className="font-medium">Scan an authorized hostname</span>
              <input
                className="aegis-input font-mono"
                placeholder="example.com"
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
              className="aegis-btn aegis-btn-accent min-w-[8.5rem]"
            >
              {scanning ? "Scanning…" : "Scan now"}
            </button>
          </form>

          {scanError ? (
            <p role="alert" className="text-sm font-medium text-danger">
              {scanError}
            </p>
          ) : null}
          {scanMessage ? (
            <p
              role="status"
              className="rounded-lg border border-accent/20 bg-accent-soft px-3 py-2 text-sm text-accent"
            >
              {scanMessage}
            </p>
          ) : null}

          <DomainList domains={domains} loading={loading} error={error} today={today} />
        </section>

        <div className="aegis-rise" style={{ animationDelay: "240ms" }}>
          <MandateSetup domains={listForMandate} apiBaseUrl={apiBaseUrl} />
        </div>
      </div>
    </main>
  );
}
