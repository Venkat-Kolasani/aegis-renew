import Link from "next/link";

import SiteChrome from "@/components/SiteChrome";

const pipeline = [
  {
    step: "01",
    title: "Detect",
    body: "RDAP domain expiry, crt.sh TLS signals, and bounded DNS takeover checks on hostnames you authorize.",
  },
  {
    step: "02",
    title: "Mandate",
    body: "Approve a merchant-locked yearly Prava cap once with a passkey. That standing authority is what makes renewals autonomous later.",
  },
  {
    step: "03",
    title: "Rank with OpenAI",
    body: "gpt-4o-mini scores urgency from live scan fields; deterministic policy keeps or downgrades auto_renew. Ranking never spends money.",
  },
  {
    step: "04",
    title: "Renew autonomously",
    body: "Only when a final auto_renew decision, active mandate, merchant lock, and quote under the cap all align—then Aegis completes checkout.",
  },
] as const;

export default function LandingPage() {
  return (
    <SiteChrome variant="marketing">
      <div className="aegis-landing-grid">
        <section className="relative overflow-hidden border-b border-line">
          <div className="aegis-hero-glow pointer-events-none absolute inset-0" aria-hidden />
          <div className="relative mx-auto grid max-w-6xl gap-12 px-5 py-16 sm:px-8 lg:grid-cols-[1.15fr_0.85fr] lg:py-24">
            <div className="aegis-rise space-y-8">
              <p className="inline-flex items-center gap-2 rounded-full border border-line bg-bg-elevated px-3 py-1 text-xs font-medium text-ink-muted">
                <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />
                Infrastructure risk, not another billing dashboard
              </p>
              <div className="space-y-5">
                <h1 className="font-display text-[2.35rem] font-semibold leading-[1.08] tracking-tight text-ink sm:text-5xl lg:text-[3.25rem]">
                  Renewal happens on{" "}
                  <span className="text-accent">your terms</span>, not the registrar&apos;s clock.
                </h1>
                <p className="max-w-xl text-base leading-relaxed text-ink-muted sm:text-lg">
                  Expired domains and TLS certs take sites offline without warning. Aegis detects the
                  risk, ranks it with OpenAI, and renews through a user-approved Prava mandate—so the
                  agent can pay later without another passkey when coverage still matches.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <Link href="/dashboard" className="aegis-btn aegis-btn-primary px-5 py-2.5">
                  Open operations console
                </Link>
                <a href="#how-it-works" className="aegis-btn aegis-btn-secondary px-5 py-2.5">
                  See the flow
                </a>
              </div>
            </div>

            <aside className="aegis-rise aegis-panel rounded-2xl p-6 lg:mt-4" style={{ animationDelay: "120ms" }}>
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-ink-faint">
                What you get today
              </p>
              <ul className="mt-5 space-y-4 text-sm leading-relaxed text-ink-muted">
                <li className="flex gap-3">
                  <span className="mt-0.5 font-mono text-xs text-accent">✓</span>
                  Live scan + inventory against your Aegis API
                </li>
                <li className="flex gap-3">
                  <span className="mt-0.5 font-mono text-xs text-accent">✓</span>
                  OpenAI ranking gated by mandate coverage policy
                </li>
                <li className="flex gap-3">
                  <span className="mt-0.5 font-mono text-xs text-accent">✓</span>
                  Autonomous sandbox renewal under a yearly Prava mandate
                </li>
              </ul>
              <div className="mt-6 border-t border-line pt-5">
                <p className="font-mono text-[11px] leading-relaxed text-ink-faint">
                  Passkey once → OpenAI ranks → policy renews only when the mandate still covers the
                  quote. DEMO registrar checkout is disclosed in the README.
                </p>
              </div>
            </aside>
          </div>
        </section>

        <section id="how-it-works" className="mx-auto max-w-6xl px-5 py-16 sm:px-8 sm:py-20">
          <div className="max-w-2xl">
            <h2 className="font-display text-2xl font-semibold text-ink sm:text-3xl">
              One pipeline, four hard gates
            </h2>
            <p className="mt-3 text-sm leading-relaxed text-ink-muted sm:text-base">
              Each stage is deliberately isolated. Detection can fail partially without lying about
              health. Ranking never charges. Execution never guesses mandate limits.
            </p>
          </div>

          <ol className="mt-10 grid gap-4 lg:grid-cols-2">
            {pipeline.map((item, index) => (
              <li
                key={item.step}
                className="aegis-rise group relative overflow-hidden rounded-xl border border-line bg-bg-elevated p-6 transition-shadow duration-300 hover:shadow-[0_8px_30px_rgba(12,18,34,0.06)]"
                style={{ animationDelay: `${80 + index * 60}ms` }}
              >
                <span className="font-mono text-xs font-medium text-accent">{item.step}</span>
                <h3 className="mt-2 font-display text-xl font-semibold text-ink">{item.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-ink-muted">{item.body}</p>
                <div
                  className="pointer-events-none absolute -right-6 -top-6 h-24 w-24 rounded-full bg-accent/5 transition-transform duration-500 group-hover:scale-110"
                  aria-hidden
                />
              </li>
            ))}
          </ol>
        </section>

        <section className="border-y border-line bg-[#0c1222] text-[#e8ecf3]">
          <div className="mx-auto grid max-w-6xl gap-8 px-5 py-14 sm:px-8 lg:grid-cols-[1fr_auto] lg:items-center">
            <div>
              <h2 className="font-display text-2xl font-semibold sm:text-3xl">
                Ready to scan your first hostname?
              </h2>
              <p className="mt-3 max-w-xl text-sm leading-relaxed text-[#a8b0c0] sm:text-base">
                Start the API, open the console, and run a scan on a domain you control. Mandate
                setup uses the same inventory—no fixture seeding.
              </p>
            </div>
            <Link
              href="/dashboard"
              className="aegis-btn aegis-btn-invert shrink-0 justify-self-start lg:justify-self-end"
            >
              Go to dashboard
            </Link>
          </div>
        </section>
      </div>
    </SiteChrome>
  );
}
