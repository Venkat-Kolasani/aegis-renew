import type { DomainSummary } from "@/lib/aegisApi";

import RiskBadge, { daysUntilExpiry } from "./RiskBadge";

export type { DomainSummary };

type DomainListProps = {
  domains: DomainSummary[];
  loading?: boolean;
  error?: string | null;
  today?: Date;
};

const displayDate = (value: string | null) => value?.slice(0, 10) ?? "Not available";
const displayDays = (value: string | null, today: Date) => {
  const days = daysUntilExpiry(value, today);
  if (days === null) return "No date";
  if (days < 0) return `${Math.abs(days)}d overdue`;
  if (days === 0) return "Due today";
  return `${days}d remaining`;
};

const displayDateTime = (value: string | null) => {
  if (!value) return "Not available";

  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? "Not available"
    : `${parsed.toISOString().slice(0, 16).replace("T", " ")} UTC`;
};

export default function DomainList({
  domains,
  loading = false,
  error = null,
  today,
}: DomainListProps) {
  const referenceDate = today ?? new Date();

  if (loading) {
    return (
      <div
        aria-busy="true"
        className="aegis-panel rounded-xl px-6 py-10 text-center"
      >
        <p className="text-sm font-medium text-ink">Loading domains…</p>
        <p className="mt-1 text-sm text-ink-muted">
          Aegis is reading the monitored inventory from the API.
        </p>
        <div className="mx-auto mt-6 h-1 w-40 overflow-hidden rounded-full bg-line">
          <div className="h-full w-1/2 animate-pulse rounded-full bg-accent/70" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div
        role="alert"
        className="rounded-xl border border-danger/20 bg-danger-soft px-6 py-8"
      >
        <p className="text-sm font-semibold text-danger">Domain inventory unavailable</p>
        <p className="mt-1 text-sm text-danger/80">{error}</p>
      </div>
    );
  }

  if (domains.length === 0) {
    return (
      <div className="aegis-panel rounded-xl border-dashed px-6 py-12 text-center">
        <p className="text-sm font-semibold text-ink">No domains scanned yet.</p>
        <p className="mt-1 text-sm text-ink-muted">
          Use Scan now with a hostname you own or are authorized to assess.
        </p>
      </div>
    );
  }

  return (
    <div className="aegis-panel overflow-hidden rounded-xl">
      <div className="overflow-x-auto">
        <table className="min-w-[860px] w-full text-left text-sm">
          <caption className="sr-only">Tracked domains and renewal signals</caption>
          <thead>
            <tr className="border-b border-line bg-[#f8fafb] text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-faint">
              <th className="px-5 py-3.5">Domain</th>
              <th className="px-5 py-3.5">Domain expiry</th>
              <th className="px-5 py-3.5">TLS certificate</th>
              <th className="px-5 py-3.5">Risk signal</th>
              <th className="px-5 py-3.5">Last scanned</th>
            </tr>
          </thead>
          <tbody>
            {domains.map((item) => (
              <tr
                key={item.id}
                className="border-b border-line/80 last:border-0 transition-colors duration-150 hover:bg-[#f7faf9]"
              >
                <th scope="row" className="px-5 py-4 font-medium text-ink">
                  <span className="font-mono text-[13px] tracking-tight">{item.domain}</span>
                  <span className="mt-1 block text-xs font-normal text-ink-faint">
                    Asset {String(item.id).padStart(2, "0")}
                  </span>
                </th>
                <td className="px-5 py-4">
                  <time
                    dateTime={item.expiry_date ?? undefined}
                    className="block tabular-nums text-ink"
                  >
                    {displayDate(item.expiry_date)}
                  </time>
                  <span className="mt-1 block text-xs text-ink-muted">
                    {displayDays(item.expiry_date, referenceDate)}
                  </span>
                </td>
                <td className="px-5 py-4">
                  <time
                    dateTime={item.cert_expiry_date ?? undefined}
                    className="block tabular-nums text-ink"
                  >
                    {displayDate(item.cert_expiry_date)}
                  </time>
                  <span className="mt-1 block text-xs text-ink-muted">
                    {displayDays(item.cert_expiry_date, referenceDate)}
                  </span>
                </td>
                <td className="px-5 py-4">
                  <RiskBadge
                    expiryDate={item.expiry_date}
                    certExpiryDate={item.cert_expiry_date}
                    dnsRisk={item.dns_risk}
                    today={referenceDate}
                  />
                </td>
                <td className="px-5 py-4 text-xs tabular-nums text-ink-muted">
                  <time dateTime={item.last_scanned ?? undefined}>
                    {displayDateTime(item.last_scanned)}
                  </time>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
