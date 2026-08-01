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
      className: "bg-warn-soft text-warn ring-1 ring-inset ring-warn/20",
      dotClassName: "bg-warn",
    };
  }

  if (days < 0) {
    return {
      label: "Expired",
      tone: "red",
      className: "bg-danger-soft text-danger ring-1 ring-inset ring-danger/20",
      dotClassName: "bg-danger",
    };
  }

  if (days <= 7) {
    return {
      label: "Urgent",
      tone: "red",
      className: "bg-danger-soft text-danger ring-1 ring-inset ring-danger/20",
      dotClassName: "bg-danger",
    };
  }

  if (days <= 30) {
    return {
      label: "Review soon",
      tone: "yellow",
      className: "bg-warn-soft text-warn ring-1 ring-inset ring-warn/20",
      dotClassName: "bg-warn",
    };
  }

  return {
    label: "Healthy",
    tone: "green",
    className: "bg-ok-soft text-ok ring-1 ring-inset ring-ok/20",
    dotClassName: "bg-ok",
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
    <div className="flex flex-wrap gap-1.5" aria-label="Domain risk signals">
      <span
        aria-label={`Expiry risk: ${expiryStatus.label}`}
        data-risk={expiryStatus.tone}
        className={`inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-[11px] font-semibold tracking-wide ${expiryStatus.className}`}
      >
        <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-sm ${expiryStatus.dotClassName}`} />
        {expiryStatus.label}
      </span>
      {dnsRisk ? (
        <span
          aria-label="DNS takeover risk"
          className="inline-flex items-center gap-1.5 rounded-md bg-dns-soft px-2 py-0.5 text-[11px] font-semibold tracking-wide text-dns ring-1 ring-inset ring-dns/15"
        >
          <span aria-hidden="true" className="h-1.5 w-1.5 rounded-sm bg-dns" />
          DNS takeover risk
        </span>
      ) : (
        <span className="inline-flex items-center rounded-md bg-[#f1f4f8] px-2 py-0.5 text-[11px] font-medium tracking-wide text-ink-faint ring-1 ring-inset ring-line">
          DNS clear
        </span>
      )}
    </div>
  );
}
