/** Same-origin helpers for Aegis FastAPI routes via the Next rewrite. */

export type DomainSummary = {
  id: number;
  domain: string;
  expiry_date: string | null;
  cert_expiry_date: string | null;
  dns_risk: boolean;
  last_scanned: string | null;
};

export type ScanResult = {
  id: number;
  domain: string;
  expiry_date: string | null;
  cert_expiry_date: string | null;
  dns_risk: boolean;
  dns_risk_detail: string | null;
};

const DEFAULT_API_BASE = "/aegis-api";
const FETCH_TIMEOUT_MS = 30_000;

export function resolveApiBase(apiBaseUrl?: string): string {
  if (apiBaseUrl && apiBaseUrl.length > 0) return apiBaseUrl.replace(/\/$/, "");
  return DEFAULT_API_BASE;
}

/** Lightweight client check before POST /scan; backend still owns final validation. */
export function isValidScanDomain(raw: string): boolean {
  const candidate = raw.trim().toLowerCase().replace(/\.$/, "");
  if (!candidate) return false;
  if (candidate.includes("://") || candidate.includes("/") || candidate.includes("\\")) {
    return false;
  }
  if (candidate.includes(":") || candidate.includes("*")) return false;
  // Reject dotted-decimal IPv4 so the client matches backend normalize_domain.
  if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(candidate)) return false;
  if (candidate.length > 253) return false;
  const labels = candidate.split(".");
  if (labels.length < 2) return false;
  return labels.every((label) => /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label));
}

function isNullOrString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

export function parseDomainSummary(value: unknown): DomainSummary | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  if (typeof row.id !== "number" || !Number.isInteger(row.id) || row.id <= 0) {
    return null;
  }
  if (typeof row.domain !== "string" || row.domain.length === 0) return null;
  if (!isNullOrString(row.expiry_date)) return null;
  if (!isNullOrString(row.cert_expiry_date)) return null;
  if (typeof row.dns_risk !== "boolean") return null;
  if (!isNullOrString(row.last_scanned)) return null;
  if (typeof row.last_scanned === "string" && row.last_scanned.length === 0) {
    return null;
  }
  // Contract fields only — no remapping that could hide backend mistakes.
  return {
    id: row.id,
    domain: row.domain,
    expiry_date: row.expiry_date,
    cert_expiry_date: row.cert_expiry_date,
    dns_risk: row.dns_risk,
    last_scanned: row.last_scanned,
  };
}

export function parseScanResult(value: unknown): ScanResult | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  if (typeof row.id !== "number" || !Number.isInteger(row.id) || row.id <= 0) {
    return null;
  }
  if (typeof row.domain !== "string" || row.domain.length === 0) return null;
  if (!isNullOrString(row.expiry_date)) return null;
  if (!isNullOrString(row.cert_expiry_date)) return null;
  if (typeof row.dns_risk !== "boolean") return null;
  if (!isNullOrString(row.dns_risk_detail)) return null;
  return {
    id: row.id,
    domain: row.domain,
    expiry_date: row.expiry_date,
    cert_expiry_date: row.cert_expiry_date,
    dns_risk: row.dns_risk,
    dns_risk_detail: row.dns_risk_detail,
  };
}

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const payload: unknown = await response.json();
    if (
      payload &&
      typeof payload === "object" &&
      "detail" in payload &&
      typeof (payload as { detail: unknown }).detail === "string"
    ) {
      return (payload as { detail: string }).detail;
    }
  } catch {
    // Fall through to status text.
  }
  return `Request failed (${response.status})`;
}

export async function fetchDomains(apiBaseUrl?: string): Promise<DomainSummary[]> {
  const response = await fetch(`${resolveApiBase(apiBaseUrl)}/domains`, {
    method: "GET",
    headers: { Accept: "application/json" },
    cache: "no-store",
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
  if (!response.ok) {
    throw new Error(await readErrorDetail(response));
  }
  const payload: unknown = await response.json();
  if (!Array.isArray(payload)) {
    throw new Error("Domain list response was not an array");
  }
  const domains: DomainSummary[] = [];
  for (const item of payload) {
    const parsed = parseDomainSummary(item);
    if (!parsed) {
      throw new Error("Domain list contained a row that does not match the API contract");
    }
    domains.push(parsed);
  }
  return domains;
}

export async function scanDomain(
  domain: string,
  apiBaseUrl?: string,
): Promise<ScanResult> {
  const response = await fetch(`${resolveApiBase(apiBaseUrl)}/scan`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ domain }),
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
  if (!response.ok) {
    throw new Error(await readErrorDetail(response));
  }
  const payload: unknown = await response.json();
  const parsed = parseScanResult(payload);
  if (!parsed) {
    throw new Error("Scan response does not match the API contract");
  }
  return parsed;
}

export type AgentDecision = "auto_renew" | "flag_for_review" | "ignore";

export type RankDecision = {
  domain_id: number;
  criticality_score: number;
  decision: AgentDecision;
  reason: string;
};

export type PaymentExecutionResult = {
  payment_status: string;
  merchant_order_ref: string | null;
  completed: boolean;
};

export type MandateReconciliationResult = {
  status: "active";
};

const RANK_TIMEOUT_MS = 90_000;

const RANK_TIMEOUT_MESSAGE = "Ranking timed out. Try again.";
const RANK_MALFORMED_MESSAGE = "Rank response was malformed";

function isTimeoutError(err: unknown): boolean {
  if (!err || typeof err !== "object") return false;
  const name = (err as { name?: unknown }).name;
  return name === "TimeoutError" || name === "AbortError";
}

export function parseRankDecision(value: unknown): RankDecision | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  if (typeof row.domain_id !== "number" || !Number.isInteger(row.domain_id) || row.domain_id <= 0) {
    return null;
  }
  if (
    typeof row.criticality_score !== "number" ||
    !Number.isInteger(row.criticality_score) ||
    row.criticality_score < 0 ||
    row.criticality_score > 100
  ) {
    return null;
  }
  if (
    row.decision !== "auto_renew" &&
    row.decision !== "flag_for_review" &&
    row.decision !== "ignore"
  ) {
    return null;
  }
  if (typeof row.reason !== "string" || row.reason.trim().length === 0) {
    return null;
  }
  return {
    domain_id: row.domain_id,
    criticality_score: row.criticality_score,
    decision: row.decision,
    reason: row.reason,
  };
}

export function parsePaymentExecutionResult(
  value: unknown,
): PaymentExecutionResult | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  if (typeof row.payment_status !== "string" || row.payment_status.length === 0) {
    return null;
  }
  if (!isNullOrString(row.merchant_order_ref)) return null;
  if (typeof row.completed !== "boolean") return null;
  return {
    payment_status: row.payment_status,
    merchant_order_ref: row.merchant_order_ref,
    completed: row.completed,
  };
}

export async function rankDomains(
  domainIds: number[],
  apiBaseUrl?: string,
): Promise<RankDecision[]> {
  let response: Response;
  try {
    response = await fetch(`${resolveApiBase(apiBaseUrl)}/agent/rank`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ domain_ids: domainIds }),
      signal: AbortSignal.timeout(RANK_TIMEOUT_MS),
    });
  } catch (err) {
    if (isTimeoutError(err)) {
      throw new Error(RANK_TIMEOUT_MESSAGE);
    }
    throw err;
  }
  if (!response.ok) {
    throw new Error(await readErrorDetail(response));
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error(RANK_MALFORMED_MESSAGE);
  }
  if (!Array.isArray(payload)) {
    throw new Error("Rank response was not an array");
  }
  const decisions: RankDecision[] = [];
  for (const item of payload) {
    const parsed = parseRankDecision(item);
    if (!parsed) {
      throw new Error("Rank response contained a row that does not match the API contract");
    }
    decisions.push(parsed);
  }
  return decisions;
}

export async function executePayment(
  domainId: number,
  apiBaseUrl?: string,
): Promise<PaymentExecutionResult> {
  if (!Number.isInteger(domainId) || domainId <= 0) {
    throw new Error("Select a valid domain for renewal execution.");
  }
  const response = await fetch(`${resolveApiBase(apiBaseUrl)}/payments/execute`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    // Security boundary: the browser supplies only the monitored database id.
    body: JSON.stringify({ domain_id: domainId }),
    signal: AbortSignal.timeout(RANK_TIMEOUT_MS),
  });
  if (!response.ok) {
    throw new Error(await readErrorDetail(response));
  }
  const payload: unknown = await response.json();
  const parsed = parsePaymentExecutionResult(payload);
  if (!parsed) {
    throw new Error("Payment response does not match the API contract");
  }
  return parsed;
}

export async function reconcileMandate(
  domainId: number,
  apiBaseUrl?: string,
): Promise<MandateReconciliationResult> {
  if (!Number.isInteger(domainId) || domainId <= 0) {
    throw new Error("Select a valid domain for mandate reconciliation.");
  }
  const response = await fetch(
    `${resolveApiBase(apiBaseUrl)}/payments/mandate/reconcile`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ domain_id: domainId }),
    },
  );
  if (!response.ok) {
    throw new Error(await readErrorDetail(response));
  }
  const payload: unknown = await response.json();
  if (
    !payload ||
    typeof payload !== "object" ||
    (payload as Record<string, unknown>).status !== "active"
  ) {
    throw new Error("Mandate reconciliation response is invalid");
  }
  return { status: "active" };
}
