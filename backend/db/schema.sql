-- Aegis Phase 0 Postgres schema. No payment credential is stored here.

CREATE TABLE domains (
    id BIGSERIAL PRIMARY KEY,
    domain TEXT NOT NULL UNIQUE,
    expiry_date DATE,
    cert_expiry_date TIMESTAMPTZ,
    dns_risk BOOLEAN NOT NULL DEFAULT FALSE,
    dns_risk_detail TEXT,
    last_scanned TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE agent_decisions (
    id BIGSERIAL PRIMARY KEY,
    domain_id BIGINT NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    criticality_score SMALLINT NOT NULL CHECK (criticality_score BETWEEN 0 AND 100),
    decision TEXT NOT NULL CHECK (decision IN ('auto_renew', 'flag_for_review', 'ignore')),
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE mandates (
    id BIGSERIAL PRIMARY KEY,
    domain_id BIGINT NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    provider_mandate_id TEXT NOT NULL UNIQUE,
    merchant_name TEXT NOT NULL,
    merchant_url TEXT NOT NULL,
    merchant_country CHAR(2) NOT NULL,
    cap_amount NUMERIC(12, 2) NOT NULL CHECK (cap_amount > 0),
    currency CHAR(3) NOT NULL,
    frequency TEXT NOT NULL CHECK (frequency = 'yearly'),
    status TEXT NOT NULL,
    valid_until TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE payment_attempts (
    id BIGSERIAL PRIMARY KEY,
    domain_id BIGINT NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    mandate_id BIGINT NOT NULL REFERENCES mandates(id) ON DELETE RESTRICT,
    amount NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    merchant_order_ref TEXT,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
