type RiskBadgeProps = {
  dnsRisk: boolean;
};

export default function RiskBadge({ dnsRisk }: RiskBadgeProps) {
  const label = dnsRisk ? "DNS risk" : "Clear";

  return (
    <span
      aria-label={`DNS risk status: ${label}`}
      className={`inline-flex rounded-full px-2.5 py-1 text-xs font-medium ${
        dnsRisk
          ? "bg-rose-400/15 text-rose-200 ring-1 ring-inset ring-rose-300/30"
          : "bg-emerald-400/15 text-emerald-200 ring-1 ring-inset ring-emerald-300/30"
      }`}
    >
      {label}
    </span>
  );
}
