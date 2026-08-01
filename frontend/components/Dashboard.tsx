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
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,_#164e63_0%,_#020617_42%,_#020617_100%)] px-6 py-12 text-slate-100">
      <section className="mx-auto max-w-6xl space-y-10">
        <header className="space-y-4">
          <p className="text-sm font-medium tracking-[0.24em] text-cyan-300">AEGIS</p>
          <div className="max-w-3xl space-y-3">
            <h1 className="text-4xl font-semibold tracking-tight text-white">
              Infrastructure renewal, under your control.
            </h1>
            <p className="text-base leading-relaxed text-slate-300">
              Live domain, certificate, and DNS takeover signals from the local Aegis API.
              Ranking and payments stay separate from this inventory view.
            </p>
          </div>
        </header>

        <dl className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-2xl border border-slate-800/80 bg-slate-950/50 p-5">
            <dt className="text-xs uppercase tracking-[0.16em] text-slate-500">Monitored</dt>
            <dd className="mt-2 text-3xl font-semibold text-white">{summary.monitored}</dd>
          </div>
          <div className="rounded-2xl border border-rose-400/20 bg-rose-400/10 p-5">
            <dt className="text-xs uppercase tracking-[0.16em] text-rose-200/80">Urgent</dt>
            <dd className="mt-2 text-3xl font-semibold text-rose-100">{summary.urgent}</dd>
          </div>
          <div className="rounded-2xl border border-amber-400/20 bg-amber-400/10 p-5">
            <dt className="text-xs uppercase tracking-[0.16em] text-amber-200/80">Review soon</dt>
            <dd className="mt-2 text-3xl font-semibold text-amber-100">{summary.reviewSoon}</dd>
          </div>
          <div className="rounded-2xl border border-violet-400/20 bg-violet-400/10 p-5">
            <dt className="text-xs uppercase tracking-[0.16em] text-violet-200/80">DNS risk</dt>
            <dd className="mt-2 text-3xl font-semibold text-violet-100">{summary.dnsRisk}</dd>
          </div>
        </dl>

        <section className="space-y-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="text-lg font-medium text-white">Domain risk inventory</h2>
              <p className="mt-1 text-sm text-slate-400">
                Data from <code className="text-slate-300">GET /api/domains</code>. Null detector
                fields mean a partial scan, not a healthy asset.
              </p>
            </div>
            <button
              type="button"
              onClick={() => void loadDomains()}
              className="rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-xs uppercase tracking-[0.14em] text-slate-300 transition hover:border-slate-500"
              disabled={loading || scanning}
            >
              Refresh
            </button>
          </div>

          <form
            className="flex flex-col gap-3 rounded-2xl border border-slate-800 bg-slate-950/40 p-4 sm:flex-row sm:items-end"
            onSubmit={(event) => void onScan(event)}
          >
            <label className="block flex-1 space-y-1 text-sm text-slate-300">
              <span>Scan an authorized hostname</span>
              <input
                className="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-white outline-none ring-cyan-500/40 focus:ring"
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
              className="rounded-xl bg-cyan-500 px-4 py-2 text-sm font-medium text-slate-950 transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {scanning ? "Scanning…" : "Scan now"}
            </button>
          </form>

          {scanError ? (
            <p role="alert" className="text-sm text-rose-200">
              {scanError}
            </p>
          ) : null}
          {scanMessage ? (
            <p role="status" className="text-sm text-cyan-100/90">
              {scanMessage}
            </p>
          ) : null}

          <DomainList domains={domains} loading={loading} error={error} today={today} />
        </section>

        <MandateSetup domains={listForMandate} apiBaseUrl={apiBaseUrl} />
      </section>
    </main>
  );
}
