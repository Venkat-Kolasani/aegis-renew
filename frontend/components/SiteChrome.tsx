import Link from "next/link";
import type { ReactNode } from "react";

type SiteChromeProps = {
  children: ReactNode;
  variant?: "marketing" | "console";
};

export default function SiteChrome({ children, variant = "marketing" }: SiteChromeProps) {
  return (
    <div className={variant === "console" ? "aegis-console-shell min-h-screen" : "min-h-screen"}>
      <header className="border-b border-line/80 bg-bg-elevated/90 backdrop-blur-sm">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-5 py-4 sm:px-8">
          <Link href="/" className="group flex items-baseline gap-2.5">
            <span className="font-display text-xl font-semibold tracking-tight text-ink">
              Aegis
            </span>
            <span className="hidden text-[11px] font-medium uppercase tracking-[0.2em] text-ink-faint sm:inline">
              Agentic renewal
            </span>
          </Link>
          <nav className="flex items-center gap-2 sm:gap-3" aria-label="Primary">
            {variant === "marketing" ? (
              <>
                <a
                  href="#how-it-works"
                  className="aegis-btn aegis-btn-ghost hidden sm:inline-flex"
                >
                  How it works
                </a>
                <Link href="/dashboard" className="aegis-btn aegis-btn-primary">
                  Open console
                </Link>
              </>
            ) : (
              <>
                <Link href="/" className="aegis-btn aegis-btn-ghost">
                  Home
                </Link>
                <span className="rounded-md bg-[#f1f3f6] px-2.5 py-1 font-mono text-[11px] text-ink-muted">
                  console
                </span>
              </>
            )}
          </nav>
        </div>
      </header>
      {children}
      <footer className="border-t border-line/80 bg-bg-elevated">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 px-5 py-8 text-sm text-ink-muted sm:flex-row sm:items-center sm:justify-between sm:px-8">
          <p>Built for the Prava Agentic Commerce Hackathon.</p>
          <p className="font-mono text-xs text-ink-faint">
            Payments use real sandbox mandates · DEMO registrar disclosed in README
          </p>
        </div>
      </footer>
    </div>
  );
}
