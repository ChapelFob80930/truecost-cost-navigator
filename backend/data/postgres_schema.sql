-- TrueCost schema for Postgres / Tiger Data. Same shape as the SQLite file (prices, hospitals, codes).
-- Rates are NUMERIC so no float rounding; loaded_at records when a row was loaded from the hospital price files.
CREATE TABLE IF NOT EXISTS hospitals (
  hospital TEXT PRIMARY KEY,
  address  TEXT,
  lat      DOUBLE PRECISION,
  lng      DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS codes (
  code        TEXT PRIMARY KEY,
  code_type   TEXT NOT NULL,          -- CPT | HCPCS | MS-DRG | ICD10
  description TEXT,
  phase       TEXT                    -- diagnostic | treatment | diagnosis
);

CREATE TABLE IF NOT EXISTS prices (
  id              BIGSERIAL PRIMARY KEY,
  hospital        TEXT NOT NULL,
  code            TEXT NOT NULL,
  code_type       TEXT,
  description     TEXT,
  setting         TEXT,               -- outpatient | inpatient
  payer           TEXT,
  plan            TEXT,
  rate_type       TEXT,               -- negotiated_dollar | percent_of_charges
  negotiated_rate NUMERIC(14,4),
  cash_price      NUMERIC(14,4),
  gross_charge    NUMERIC(14,4),
  notes           TEXT,
  loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_prices_code_payer ON prices (code, payer);
CREATE INDEX IF NOT EXISTS idx_prices_hospital   ON prices (hospital);

-- ---- usage events (time-series) ------------------------------------------------------------------
-- Created automatically on startup by app/events.py (PostgresEventStore). Shown here for reference.
-- One row per call to a main endpoint. No note text and no personal data: only counts and fixed words.
CREATE TABLE IF NOT EXISTS app_events (
  ts          timestamptz NOT NULL DEFAULT now(),
  endpoint    text        NOT NULL,
  status      integer     NOT NULL,
  latency_ms  integer     NOT NULL,
  meta        jsonb       NOT NULL DEFAULT '{}'::jsonb
);
SELECT create_hypertable('app_events', 'ts', if_not_exists => TRUE, migrate_data => TRUE);   -- TimescaleDB on Tiger Data
CREATE INDEX IF NOT EXISTS ix_app_events_endpoint_ts ON app_events (endpoint, ts DESC);
SELECT add_retention_policy('app_events', INTERVAL '30 days', if_not_exists => TRUE);
