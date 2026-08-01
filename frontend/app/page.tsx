import DomainList, { type DomainSummary } from "@/components/DomainList";
import MandateSetup from "@/components/MandateSetup";
import { daysUntilExpiry } from "@/components/RiskBadge";

const fixtureToday = new Date("2026-08-01T00:00:00Z");

const fixtureDomains: DomainSummary[] = [
  {
    id: 1,
    domain: "docs.aegis-demo.test",
    expiry_date: "2027-03-15",
    cert_expiry_date: "2026-12-01",
    dns_risk: false,
    last_scanned: "2026-08-01T03:00:00Z",
  },
  {
    id: 2,
    domain: "billing.aegis-demo.test",
    expiry_date: "2026-08-20",
    cert_expiry_date: "2026-10-12",
    dns_risk: false,
    last_scanned: "2026-08-01T03:00:00Z",
  },
  {
    id: 3,
    domain: "api.aegis-demo.test",
    expiry_date: "2026-11-30",
    cert_expiry_date: "2026-08-04",
    dns_risk: false,
    last_scanned: "2026-08-01T03:05:00Z",
  },
  {
    id: 4,
    domain: "cdn.aegis-demo.test",
    expiry_date: "2027-01-08",
    cert_expiry_date: "2026-09-18",
    dns_risk: true,
    last_scanned: "2026-08-01T03:08:00Z",
  },
  {
    id: 5,
    domain: "legacy.aegis-demo.test",
    expiry_date: "2026-08-06",
    cert_expiry_date: "2026-08-03",
    dns_risk: true,
    last_scanned: "2026-08-01T03:10:00Z",
  },
  {
    id: 6,
    domain: "staging.aegis-demo.test",
    expiry_date: null,
    cert_expiry_date: "2026-08-22",
    dns_risk: false,
    last_scanned: "2026-08-01T03:12:00Z",
  },
  {
    id: 7,
    domain: "portal.aegis-demo.test",
    expiry_date: "2026-07-20",
    cert_expiry_date: "2026-07-18",
    dns_risk: false,
    last_scanned: "2026-08-01T03:15:00Z",
  },
];

function nearestDays(domain: DomainSummary): number | null {
  const values = [
    daysUntilExpiry(domain.expiry_date, fixtureToday),
    daysUntilExpiry(domain.cert_expiry_date, fixtureToday),
  ].filter((value): value is number => value !== null);

  if (values.length === 0) return null;
  return values.sort((left, right) => left - right)[0] ?? null;
}

function summarize(domains: DomainSummary[]) {
  let urgent = 0;
  let reviewSoon = 0;
  let dnsRisk = 0;

  for (const domain of domains) {
    if (domain.dns_risk) dnsRisk += 1;
    const days = nearestDays(domain);
    if (days === null) continue;
    if (days <= 7) urgent += 1;
    else if (days <= 30) reviewSoon += 1;
  }

  return {
    monitored: domains.length,
    urgent,
    reviewSoon,
    dnsRisk,
  };
}

export default function Home() {
  const summary = summarize(fixtureDomains);

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
              Track domain, certificate, and DNS takeover risk with fixture data until live
              detection lands. Ranking and payments stay separate from this inventory view.
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
                Contract-shaped fixtures for demo states. Live scan data arrives in VENKAT-4.
              </p>
            </div>
            <p className="text-xs uppercase tracking-[0.14em] text-slate-500">
              Fixture reference {fixtureToday.toISOString().slice(0, 10)}
            </p>
          </div>
          <DomainList domains={fixtureDomains} today={fixtureToday} />
        </section>

        <MandateSetup
          domains={fixtureDomains.map((domain) => ({
            id: domain.id,
            domain: domain.domain,
          }))}
        />
      </section>
    </main>
  );
}
