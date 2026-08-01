import RiskBadge from "./RiskBadge";

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
};

const displayDate = (value: string | null) => value?.slice(0, 10) ?? "Not available";

export default function DomainList({ domains, loading = false }: DomainListProps) {
  if (loading) {
    return <p aria-busy="true" className="rounded-xl border border-slate-800 p-6 text-slate-300">Loading domains…</p>;
  }

  if (domains.length === 0) {
    return <p className="rounded-xl border border-dashed border-slate-700 p-6 text-slate-400">No domains scanned yet.</p>;
  }

  return (
    <div className="overflow-hidden rounded-xl border border-slate-800">
      <table className="min-w-full divide-y divide-slate-800 text-left text-sm">
        <caption className="sr-only">Tracked domains and renewal signals</caption>
        <thead className="bg-slate-900/80 text-xs uppercase tracking-wide text-slate-400">
          <tr><th className="px-4 py-3">Domain</th><th className="px-4 py-3">Expiry</th><th className="px-4 py-3">Certificate</th><th className="px-4 py-3">DNS</th></tr>
        </thead>
        <tbody className="divide-y divide-slate-800">
          {domains.map((item) => (
            <tr key={item.id} className="bg-slate-950/40 text-slate-200">
              <th scope="row" className="px-4 py-4 font-medium">{item.domain}</th>
              <td className="px-4 py-4">{displayDate(item.expiry_date)}</td>
              <td className="px-4 py-4">{displayDate(item.cert_expiry_date)}</td>
              <td className="px-4 py-4"><RiskBadge dnsRisk={item.dns_risk} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
