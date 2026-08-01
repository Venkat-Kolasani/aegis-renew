import DomainList, { type DomainSummary } from "@/components/DomainList";

const fixtureDomains: DomainSummary[] = [
  {
    id: 1,
    domain: "api.aegis-demo.test",
    expiry_date: "2026-08-24",
    cert_expiry_date: "2026-08-08",
    dns_risk: false,
    last_scanned: "2026-08-01T03:00:00Z",
  },
  {
    id: 2,
    domain: "legacy.aegis-demo.test",
    expiry_date: "2026-08-04",
    cert_expiry_date: "2026-08-03",
    dns_risk: true,
    last_scanned: "2026-08-01T03:00:00Z",
  },
];

export default function Home() {
  return (
    <main className="min-h-screen bg-slate-950 px-6 py-12 text-slate-100">
      <section className="mx-auto max-w-5xl space-y-8">
        <header className="space-y-3">
          <p className="text-sm font-medium tracking-[0.2em] text-cyan-300">AEGIS</p>
          <h1 className="text-3xl font-semibold tracking-tight">Infrastructure renewal, under your control.</h1>
          <p className="max-w-2xl text-slate-300">Track expiring infrastructure and surface the next safe renewal decision.</p>
        </header>
        <DomainList domains={fixtureDomains} />
      </section>
    </main>
  );
}
