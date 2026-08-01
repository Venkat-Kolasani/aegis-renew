type RiskBadgeProps = {
  expiryDate?: string | null;
  certExpiryDate?: string | null;
  dnsRisk?: boolean;
  today?: Date;
};

type ExpiryStatus = {
  label: string;
  tone: "green" | "yellow" | "red";
  className: string;
  dotClassName: string;
};

const DAY_IN_MS = 86_400_000;

export function daysUntilExpiry(value: string | null | undefined, today: Date): number | null {
  if (!value) return null;

  const parsed = new Date(value.length === 10 ? `${value}T00:00:00Z` : value);
  if (Number.isNaN(parsed.getTime())) return null;

  return Math.ceil((parsed.getTime() - today.getTime()) / DAY_IN_MS);
}

function getExpiryStatus(
  expiryDate: string | null | undefined,
  certExpiryDate: string | null | undefined,
  today: Date,
): ExpiryStatus {
  const days = [daysUntilExpiry(expiryDate, today), daysUntilExpiry(certExpiryDate, today)]
    .filter((value): value is number => value !== null)
    .sort((left, right) => left - right)[0];

  if (days === undefined) {
    return {
      label: "Expiry unknown",
      tone: "yellow",
      className: "bg-amber-400/15 text-amber-200 ring-1 ring-inset ring-amber-300/30",
      dotClassName: "bg-amber-300",
    };
  }

  if (days < 0) {
    return {
      label: "Expired",
      tone: "red",
      className: "bg-rose-400/15 text-rose-200 ring-1 ring-inset ring-rose-300/30",
      dotClassName: "bg-rose-300",
    };
  }

  if (days <= 7) {
    return {
      label: "Urgent",
      tone: "red",
      className: "bg-rose-400/15 text-rose-200 ring-1 ring-inset ring-rose-300/30",
      dotClassName: "bg-rose-300",
    };
  }

  if (days <= 30) {
    return {
      label: "Review soon",
      tone: "yellow",
      className: "bg-amber-400/15 text-amber-200 ring-1 ring-inset ring-amber-300/30",
      dotClassName: "bg-amber-300",
    };
  }

  return {
    label: "Healthy",
    tone: "green",
    className: "bg-emerald-400/15 text-emerald-200 ring-1 ring-inset ring-emerald-300/30",
    dotClassName: "bg-emerald-300",
  };
}

export default function RiskBadge({
  expiryDate,
  certExpiryDate,
  dnsRisk = false,
  today = new Date(),
}: RiskBadgeProps) {
  const expiryStatus = getExpiryStatus(expiryDate, certExpiryDate, today);

  return (
    <div className="flex flex-wrap gap-2" aria-label="Domain risk signals">
      <span
        aria-label={`Expiry risk: ${expiryStatus.label}`}
        data-risk={expiryStatus.tone}
        className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${expiryStatus.className}`}
      >
        <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${expiryStatus.dotClassName}`} />
        {expiryStatus.label}
      </span>
      {dnsRisk ? (
        <span
          aria-label="DNS takeover risk"
          className="inline-flex items-center gap-1.5 rounded-full bg-violet-400/15 px-2.5 py-1 text-xs font-medium text-violet-200 ring-1 ring-inset ring-violet-300/30"
        >
          <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-violet-300" />
          DNS takeover risk
        </span>
      ) : (
        <span className="inline-flex items-center rounded-full bg-slate-800/80 px-2.5 py-1 text-xs font-medium text-slate-400 ring-1 ring-inset ring-slate-700">
          DNS clear
        </span>
      )}
    </div>
  );
}
