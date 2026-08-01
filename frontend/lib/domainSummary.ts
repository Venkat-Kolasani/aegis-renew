import type { DomainSummary } from "@/lib/aegisApi";
import { daysUntilExpiry } from "@/components/RiskBadge";

function nearestDays(domain: DomainSummary, today: Date): number | null {
  const values = [
    daysUntilExpiry(domain.expiry_date, today),
    daysUntilExpiry(domain.cert_expiry_date, today),
  ].filter((value): value is number => value !== null);

  if (values.length === 0) return null;
  return values.sort((left, right) => left - right)[0] ?? null;
}

export function summarizeDomains(domains: DomainSummary[], today: Date) {
  let urgent = 0;
  let reviewSoon = 0;
  let dnsRisk = 0;

  for (const domain of domains) {
    if (domain.dns_risk) dnsRisk += 1;
    const days = nearestDays(domain, today);
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
