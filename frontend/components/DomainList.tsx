import RiskBadge, { daysUntilExpiry } from "./RiskBadge";

export type DomainSummary = {
  id: number;
  domain: string;
  expiry_date: string | null;
  cert_expiry_date: string | null;
  dns_risk: boolean;
  last_scanned: string | null;
};

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
      <div aria-busy="true" className="rounded-2xl border border-slate-800 bg-slate-900/50 p-6 text-slate-300">
        <p className="text-sm font-medium text-slate-200">Loading domains…</p>
        <p className="mt-1 text-sm text-slate-500">Aegis is checking the monitored inventory.</p>
      </div>
    );
  }

  if (error) {
    return (
      <div role="alert" className="rounded-2xl border border-rose-400/25 bg-rose-400/10 p-6">
        <p className="text-sm font-medium text-rose-100">Domain inventory unavailable</p>
        <p className="mt-1 text-sm text-rose-200/75">{error}</p>
      </div>
    );
  }

  if (domains.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-700 bg-slate-900/30 p-8 text-center">
        <p className="text-sm font-medium text-slate-200">No domains scanned yet.</p>
        <p className="mt-1 text-sm text-slate-500">
          Use Scan now with a hostname you own or are authorized to assess.
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-2xl border border-slate-800/90">
      <table className="min-w-[820px] divide-y divide-slate-800 text-left text-sm">
        <caption className="sr-only">Tracked domains and renewal signals</caption>
        <thead className="bg-slate-900/80 text-[11px] uppercase tracking-[0.14em] text-slate-500">
          <tr>
            <th className="px-5 py-4">Domain</th>
            <th className="px-5 py-4">Domain expiry</th>
            <th className="px-5 py-4">TLS certificate</th>
            <th className="px-5 py-4">Risk signal</th>
            <th className="px-5 py-4">Last scanned</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/90">
          {domains.map((item) => (
            <tr key={item.id} className="bg-slate-950/30 text-slate-300 transition-colors hover:bg-slate-900/60">
              <th scope="row" className="px-5 py-4 font-medium text-slate-100">
                <span className="block">{item.domain}</span>
                <span className="mt-1 block text-xs font-normal text-slate-600">Asset {String(item.id).padStart(2, "0")}</span>
              </th>
              <td className="px-5 py-4">
                <time dateTime={item.expiry_date ?? undefined} className="block text-slate-200">{displayDate(item.expiry_date)}</time>
                <span className="mt-1 block text-xs text-slate-500">{displayDays(item.expiry_date, referenceDate)}</span>
              </td>
              <td className="px-5 py-4">
                <time dateTime={item.cert_expiry_date ?? undefined} className="block text-slate-200">{displayDate(item.cert_expiry_date)}</time>
                <span className="mt-1 block text-xs text-slate-500">{displayDays(item.cert_expiry_date, referenceDate)}</span>
              </td>
              <td className="px-5 py-4"><RiskBadge expiryDate={item.expiry_date} certExpiryDate={item.cert_expiry_date} dnsRisk={item.dns_risk} today={referenceDate} /></td>
              <td className="px-5 py-4 text-xs text-slate-500">
                <time dateTime={item.last_scanned ?? undefined}>{displayDateTime(item.last_scanned)}</time>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
