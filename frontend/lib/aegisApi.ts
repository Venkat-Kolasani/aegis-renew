/** Same-origin helpers for Aegis FastAPI routes via the Next rewrite. */

export type DomainSummary = {
  id: number;
  domain: string;
  expiry_date: string | null;
  cert_expiry_date: string | null;
  dns_risk: boolean;
  last_scanned: string;
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
  if (typeof row.last_scanned !== "string" || row.last_scanned.length === 0) {
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
