-- TRP1 Week 5: The Ledger
-- Core event store schema (append-only) + projection checkpoints + outbox.

BEGIN;

-- Needed for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS events (
  event_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  stream_id        TEXT NOT NULL,
  stream_position  BIGINT NOT NULL,
  global_position  BIGINT GENERATED ALWAYS AS IDENTITY,
  event_type       TEXT NOT NULL,
  event_version    SMALLINT NOT NULL DEFAULT 1,
  payload          JSONB NOT NULL,
  metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
  recorded_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT uq_stream_position UNIQUE (stream_id, stream_position)
);

CREATE INDEX IF NOT EXISTS idx_events_stream_id ON events (stream_id, stream_position);
CREATE INDEX IF NOT EXISTS idx_events_global_pos ON events (global_position);
CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type);
CREATE INDEX IF NOT EXISTS idx_events_recorded ON events (recorded_at);

CREATE TABLE IF NOT EXISTS event_streams (
  stream_id        TEXT PRIMARY KEY,
  aggregate_type   TEXT NOT NULL,
  current_version  BIGINT NOT NULL DEFAULT 0,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  archived_at      TIMESTAMPTZ,
  metadata         JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS projection_checkpoints (
  projection_name  TEXT PRIMARY KEY,
  last_position    BIGINT NOT NULL DEFAULT 0,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS outbox (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id         UUID NOT NULL REFERENCES events(event_id),
  destination      TEXT NOT NULL,
  payload          JSONB NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  published_at     TIMESTAMPTZ,
  attempts         SMALLINT NOT NULL DEFAULT 0
);

-- ---- Projection tables ----

CREATE TABLE IF NOT EXISTS application_summary (
  application_id          TEXT PRIMARY KEY,
  state                   TEXT,
  applicant_id            TEXT,
  requested_amount_usd    DOUBLE PRECISION,
  approved_amount_usd     DOUBLE PRECISION,
  risk_tier               TEXT,
  fraud_score             DOUBLE PRECISION,
  compliance_status       TEXT,
  decision                TEXT,
  agent_sessions_completed JSONB NOT NULL DEFAULT '[]'::jsonb,
  last_event_type         TEXT,
  last_event_at           TIMESTAMPTZ,
  human_reviewer_id       TEXT,
  final_decision_at       TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS agent_performance_ledger (
  agent_id            TEXT NOT NULL,
  model_version        TEXT NOT NULL,
  analyses_completed   BIGINT NOT NULL DEFAULT 0,
  decisions_generated  BIGINT NOT NULL DEFAULT 0,
  avg_confidence_score DOUBLE PRECISION,
  avg_duration_ms      DOUBLE PRECISION,
  approve_rate         DOUBLE PRECISION,
  decline_rate         DOUBLE PRECISION,
  refer_rate           DOUBLE PRECISION,
  human_override_rate  DOUBLE PRECISION,
  first_seen_at        TIMESTAMPTZ,
  last_seen_at         TIMESTAMPTZ,
  PRIMARY KEY (agent_id, model_version)
);

-- Compliance audit view as an append-only projection of compliance events.
CREATE TABLE IF NOT EXISTS compliance_audit_events (
  application_id     TEXT NOT NULL,
  global_position    BIGINT NOT NULL,
  recorded_at        TIMESTAMPTZ NOT NULL,
  event_type         TEXT NOT NULL,
  payload            JSONB NOT NULL,
  PRIMARY KEY (application_id, global_position)
);
CREATE INDEX IF NOT EXISTS idx_compliance_audit_recorded ON compliance_audit_events (application_id, recorded_at);

COMMIT;

