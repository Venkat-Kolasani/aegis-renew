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
  /** DEMO defaults from JOINT-2 until the self-owned registrar URL is live. */
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
  // DEMO: JOINT-2 selected self-owned demo registrar; placeholder URL until VENKAT-3.
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
      className="space-y-4 rounded-2xl border border-slate-800/80 bg-slate-950/50 p-6"
      data-state={state}
    >
      <div className="space-y-2">
        <h2 id="mandate-setup-heading" className="text-lg font-medium text-white">
          Yearly renewal mandate
        </h2>
        <p className="text-sm leading-relaxed text-slate-400">
          Approve a merchant-locked yearly Prava mandate with a passkey. The browser never chooses a
          mandate id, network token, or dynamic CVV.
        </p>
        <p className="text-xs text-amber-200/80">
          {/* DEMO: JOINT-2 merchant path */}
          DEMO merchant defaults: Aegis Demo Registrar. Swap the URL after the VENKAT-3 checkout
          adapter is live.
        </p>
      </div>

      <form className="grid gap-4 sm:grid-cols-2" onSubmit={onSubmit}>
        <label className="space-y-1 text-sm text-slate-300 sm:col-span-2">
          <span>Domain</span>
          <select
            className="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-white"
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

        <label className="space-y-1 text-sm text-slate-300">
          <span>Merchant name</span>
          <input
            className="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-white"
            value={merchantName}
            onChange={(event) => setMerchantName(event.target.value)}
            disabled={state === "loading" || state === "awaiting_approval"}
            required
          />
        </label>

        <label className="space-y-1 text-sm text-slate-300">
          <span>Merchant URL</span>
          <input
            className="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-white"
            type="url"
            value={merchantUrl}
            onChange={(event) => setMerchantUrl(event.target.value)}
            disabled={state === "loading" || state === "awaiting_approval"}
            required
          />
        </label>

        <label className="space-y-1 text-sm text-slate-300">
          <span>Merchant country</span>
          <input
            className="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-white"
            value={merchantCountry}
            maxLength={2}
            onChange={(event) => setMerchantCountry(event.target.value.toUpperCase())}
            disabled={state === "loading" || state === "awaiting_approval"}
            required
          />
        </label>

        <label className="space-y-1 text-sm text-slate-300">
          <span>Cap amount</span>
          <input
            className="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-white"
            inputMode="decimal"
            value={capAmount}
            onChange={(event) => setCapAmount(event.target.value)}
            disabled={state === "loading" || state === "awaiting_approval"}
            required
          />
        </label>

        <label className="space-y-1 text-sm text-slate-300">
          <span>Currency</span>
          <input
            className="w-full rounded-xl border border-slate-700 bg-slate-900 px-3 py-2 text-white"
            value={currency}
            maxLength={3}
            onChange={(event) => setCurrency(event.target.value.toUpperCase())}
            disabled={state === "loading" || state === "awaiting_approval"}
            required
          />
        </label>

        <div className="space-y-1 text-sm text-slate-300">
          <span>Frequency</span>
          <p className="rounded-xl border border-slate-800 bg-slate-900/80 px-3 py-2 text-slate-200">
            yearly (locked)
          </p>
        </div>

        <div className="flex flex-wrap gap-3 sm:col-span-2">
          <button
            type="submit"
            className="rounded-xl bg-cyan-500 px-4 py-2 text-sm font-medium text-slate-950 disabled:cursor-not-allowed disabled:opacity-60"
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
              <button
                type="button"
                className="rounded-xl border border-slate-600 px-4 py-2 text-sm text-slate-200"
                onClick={onCancel}
              >
                Cancel
              </button>
              <button
                type="button"
                className="rounded-xl border border-amber-500/40 px-4 py-2 text-sm text-amber-100"
                onClick={onMarkExpired}
              >
                Session expired
              </button>
              {approvalUrl ? (
                <button
                  type="button"
                  className="rounded-xl border border-cyan-500/40 px-4 py-2 text-sm text-cyan-100"
                  onClick={() => openApprovalUrl(approvalUrl)}
                >
                  Reopen approval
                </button>
              ) : null}
            </>
          ) : null}

          {state === "cancelled" || state === "error" || state === "expired" ? (
            <button
              type="button"
              className="rounded-xl border border-slate-600 px-4 py-2 text-sm text-slate-200"
              onClick={onReset}
            >
              Try again
            </button>
          ) : null}
        </div>
      </form>

      {state === "loading" ? (
        <p role="status" className="text-sm text-slate-300">
          Creating a Prava mandate-setup session…
        </p>
      ) : null}

      {state === "awaiting_approval" ? (
        <p role="status" className="text-sm text-cyan-100">
          Approve with your passkey in the Prava window. Use the team sandbox card when prompted.
        </p>
      ) : null}

      {state === "cancelled" ? (
        <p role="status" className="text-sm text-slate-300">
          Mandate approval cancelled. No mandate id or payment credential was stored in the browser.
        </p>
      ) : null}

      {state === "expired" || state === "error" ? (
        <p role="alert" className="text-sm text-rose-200">
          {errorMessage}
        </p>
      ) : null}
    </section>
  );
}
