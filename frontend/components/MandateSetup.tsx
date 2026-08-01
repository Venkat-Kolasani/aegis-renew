"use client";

import { FormEvent, useMemo, useState } from "react";

export type MandateDomainOption = {
  id: number;
  domain: string;
};

export type MandateUiState =
  | "idle"
  | "loading"
  | "awaiting_approval"
  | "cancelled"
  | "error"
  | "expired";

export type MandateSetupProps = {
  domains: MandateDomainOption[];
  apiBaseUrl?: string;
  /** DEMO defaults: Aegis Demo Registrar (checkout via /api/demo/registrar/*). */
  defaultMerchantName?: string;
  defaultMerchantUrl?: string;
  defaultMerchantCountry?: string;
  defaultCapAmount?: number;
  defaultCurrency?: string;
  openApprovalUrl?: (url: string) => void;
  /** Test/demo seam for rendering non-idle states without a live Prava session. */
  initialState?: MandateUiState;
  initialErrorMessage?: string | null;
  initialApprovalUrl?: string | null;
};

export function buildMandateRequestBody(input: {
  domainId: number;
  merchantName: string;
  merchantUrl: string;
  merchantCountry: string;
  capAmount: number;
  currency: string;
}): Record<string, string | number> {
  return {
    domain_id: input.domainId,
    merchant_name: input.merchantName.trim(),
    merchant_url: input.merchantUrl.trim(),
    merchant_country: input.merchantCountry.trim().toUpperCase(),
    cap_amount: input.capAmount,
    currency: input.currency.trim().toUpperCase(),
    frequency: "yearly",
  };
}

export function isSafeHttpsUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:";
  } catch {
    return false;
  }
}

type MandateApiResponse = {
  status: string;
  approval_url: string;
};

function resolveApiBase(apiBaseUrl?: string): string {
  // Same-origin Next rewrite avoids browser CORS and keeps secrets on the API.
  if (apiBaseUrl && apiBaseUrl.length > 0) return apiBaseUrl.replace(/\/$/, "");
  return "/aegis-api";
}

function initialDomainId(domains: MandateDomainOption[]): number | null {
  const first = domains[0];
  return first && first.id > 0 ? first.id : null;
}

export default function MandateSetup({
  domains,
  apiBaseUrl,
  // DEMO: JOINT-2/VENKAT-3 self-owned registrar; mandate URL stays example.com.
  defaultMerchantName = "Aegis Demo Registrar",
  defaultMerchantUrl = "https://example.com",
  defaultMerchantCountry = "US",
  defaultCapAmount = 18,
  defaultCurrency = "USD",
  openApprovalUrl = (url: string) => {
    if (typeof window !== "undefined") {
      window.open(url, "_blank", "noopener,noreferrer");
    }
  },
  initialState,
  initialErrorMessage = null,
  initialApprovalUrl = null,
}: MandateSetupProps) {
  const sortedDomains = useMemo(
    () => [...domains].sort((left, right) => left.domain.localeCompare(right.domain)),
    [domains],
  );

  const [domainId, setDomainId] = useState<number | null>(() => initialDomainId(sortedDomains));
  const [merchantName, setMerchantName] = useState(defaultMerchantName);
  const [merchantUrl, setMerchantUrl] = useState(defaultMerchantUrl);
  const [merchantCountry, setMerchantCountry] = useState(defaultMerchantCountry);
  const [capAmount, setCapAmount] = useState(String(defaultCapAmount));
  const [currency, setCurrency] = useState(defaultCurrency);
  const [state, setState] = useState<MandateUiState>(
    initialState ?? (sortedDomains.length ? "idle" : "error"),
  );
  const [errorMessage, setErrorMessage] = useState<string | null>(
    initialErrorMessage ??
      (sortedDomains.length ? null : "No domains available for mandate setup."),
  );
  const [approvalUrl, setApprovalUrl] = useState<string | null>(initialApprovalUrl);

  const selectedDomainId = useMemo(() => {
    if (sortedDomains.length === 0) return null;
    if (domainId !== null && sortedDomains.some((domain) => domain.id === domainId)) {
      return domainId;
    }
    return initialDomainId(sortedDomains);
  }, [sortedDomains, domainId]);

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (state === "loading") return;

    setState("loading");
    setErrorMessage(null);
    setApprovalUrl(null);

    if (selectedDomainId === null || selectedDomainId <= 0) {
      setState("error");
      setErrorMessage("Select a valid domain before starting mandate approval.");
      return;
    }

    const parsedCap = Number(capAmount);
    if (!Number.isFinite(parsedCap) || parsedCap <= 0) {
      setState("error");
      setErrorMessage("Cap amount must be a positive number.");
      return;
    }

    try {
      const response = await fetch(`${resolveApiBase(apiBaseUrl)}/payments/mandate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          buildMandateRequestBody({
            domainId: selectedDomainId,
            merchantName,
            merchantUrl,
            merchantCountry,
            capAmount: parsedCap,
            currency,
          }),
        ),
      });

      const payload = (await response.json().catch(() => null)) as
        | MandateApiResponse
        | { detail?: string }
        | null;

      if (!response.ok) {
        const detail =
          payload && "detail" in payload && typeof payload.detail === "string"
            ? payload.detail
            : `Mandate setup failed (HTTP ${response.status})`;
        setState("error");
        setErrorMessage(detail);
        return;
      }

      if (!payload || !("approval_url" in payload) || typeof payload.approval_url !== "string") {
        setState("error");
        setErrorMessage("Mandate setup returned an invalid approval URL.");
        return;
      }

      if (!isSafeHttpsUrl(payload.approval_url)) {
        setState("error");
        setErrorMessage("Mandate setup returned an invalid approval URL.");
        return;
      }

      setApprovalUrl(payload.approval_url);
      setState("awaiting_approval");
      openApprovalUrl(payload.approval_url);
    } catch {
      setState("error");
      setErrorMessage("Could not reach the Aegis payment API.");
    }
  }

  function onCancel(): void {
    setState("cancelled");
    setErrorMessage(null);
  }

  function onMarkExpired(): void {
    setState("expired");
    setErrorMessage("The Prava approval session expired. Start again to mint a fresh session.");
  }

  function onReset(): void {
    setState("idle");
    setErrorMessage(null);
    setApprovalUrl(null);
  }

  return (
    <section
      aria-labelledby="mandate-setup-heading"
      className="aegis-panel space-y-5 rounded-xl p-6 sm:p-7"
      data-state={state}
    >
      <div className="space-y-2 border-b border-line pb-5">
        <h2 id="mandate-setup-heading" className="text-base font-semibold text-ink">
          Yearly renewal mandate
        </h2>
        <p className="max-w-2xl text-sm leading-relaxed text-ink-muted">
          Approve a merchant-locked yearly Prava mandate with a passkey. The browser never chooses a
          mandate id, network token, or dynamic CVV.
        </p>
        <p className="rounded-md border border-warn/20 bg-warn-soft px-3 py-2 text-xs text-warn">
          {/* DEMO: JOINT-2 / VENKAT-3 merchant path */}
          DEMO merchant: Aegis Demo Registrar ($18/year). Checkout completes via the Aegis DEMO
          adapter, not a live registrar storefront.
        </p>
      </div>

      <form className="grid gap-4 sm:grid-cols-2" onSubmit={onSubmit}>
        <label className="space-y-1.5 text-sm text-ink sm:col-span-2">
          <span className="font-medium">Domain</span>
          <select
            className="aegis-input font-mono"
            value={selectedDomainId ?? ""}
            onChange={(event) => {
              const nextId = Number(event.target.value);
              setDomainId(Number.isFinite(nextId) && nextId > 0 ? nextId : null);
            }}
            disabled={state === "loading" || state === "awaiting_approval"}
            required
          >
            {sortedDomains.map((domain) => (
              <option key={domain.id} value={domain.id}>
                {domain.domain}
              </option>
            ))}
          </select>
        </label>

        <label className="space-y-1.5 text-sm text-ink">
          <span className="font-medium">Merchant name</span>
          <input
            className="aegis-input"
            value={merchantName}
            onChange={(event) => setMerchantName(event.target.value)}
            disabled={state === "loading" || state === "awaiting_approval"}
            required
          />
        </label>

        <label className="space-y-1.5 text-sm text-ink">
          <span className="font-medium">Merchant URL</span>
          <input
            className="aegis-input font-mono"
            type="url"
            value={merchantUrl}
            onChange={(event) => setMerchantUrl(event.target.value)}
            disabled={state === "loading" || state === "awaiting_approval"}
            required
          />
        </label>

        <label className="space-y-1.5 text-sm text-ink">
          <span className="font-medium">Merchant country</span>
          <input
            className="aegis-input font-mono uppercase"
            value={merchantCountry}
            maxLength={2}
            onChange={(event) => setMerchantCountry(event.target.value.toUpperCase())}
            disabled={state === "loading" || state === "awaiting_approval"}
            required
          />
        </label>

        <label className="space-y-1.5 text-sm text-ink">
          <span className="font-medium">Cap amount</span>
          <input
            className="aegis-input font-mono"
            inputMode="decimal"
            value={capAmount}
            onChange={(event) => setCapAmount(event.target.value)}
            disabled={state === "loading" || state === "awaiting_approval"}
            required
          />
        </label>

        <label className="space-y-1.5 text-sm text-ink">
          <span className="font-medium">Currency</span>
          <input
            className="aegis-input font-mono uppercase"
            value={currency}
            maxLength={3}
            onChange={(event) => setCurrency(event.target.value.toUpperCase())}
            disabled={state === "loading" || state === "awaiting_approval"}
            required
          />
        </label>

        <div className="space-y-1.5 text-sm text-ink">
          <span className="font-medium">Frequency</span>
          <p className="aegis-input bg-[#f8f9fb] text-ink-muted">yearly (locked)</p>
        </div>

        <div className="flex flex-wrap gap-2.5 sm:col-span-2">
          <button
            type="submit"
            className="aegis-btn aegis-btn-primary"
            disabled={
              state === "loading" ||
              state === "awaiting_approval" ||
              !sortedDomains.length ||
              selectedDomainId === null
            }
          >
            {state === "loading" ? "Creating mandate session…" : "Start passkey approval"}
          </button>

          {state === "awaiting_approval" ? (
            <>
              <button type="button" className="aegis-btn aegis-btn-secondary" onClick={onCancel}>
                Cancel
              </button>
              <button type="button" className="aegis-btn aegis-btn-secondary" onClick={onMarkExpired}>
                Session expired
              </button>
              {approvalUrl ? (
                <button
                  type="button"
                  className="aegis-btn aegis-btn-accent"
                  onClick={() => openApprovalUrl(approvalUrl)}
                >
                  Reopen approval
                </button>
              ) : null}
            </>
          ) : null}

          {state === "cancelled" || state === "error" || state === "expired" ? (
            <button type="button" className="aegis-btn aegis-btn-secondary" onClick={onReset}>
              Try again
            </button>
          ) : null}
        </div>
      </form>

      {state === "loading" ? (
        <p role="status" className="text-sm text-ink-muted">
          Creating a Prava mandate-setup session…
        </p>
      ) : null}

      {state === "awaiting_approval" ? (
        <p
          role="status"
          className="rounded-lg border border-accent/20 bg-accent-soft px-3 py-2 text-sm text-accent"
        >
          Approve with your passkey in the Prava window. Use the team sandbox card when prompted.
        </p>
      ) : null}

      {state === "cancelled" ? (
        <p role="status" className="text-sm text-ink-muted">
          Mandate approval cancelled. No mandate id or payment credential was stored in the browser.
        </p>
      ) : null}

      {state === "expired" || state === "error" ? (
        <p role="alert" className="rounded-lg border border-danger/20 bg-danger-soft px-3 py-2 text-sm text-danger">
          {errorMessage}
        </p>
      ) : null}
    </section>
  );
}
