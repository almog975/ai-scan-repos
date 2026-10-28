-- AI Scan Repos – target PostgreSQL schema v1 (Phase 0 contract, per project plan "מודל נתונים").
-- Location: infra/db/ (raw SQL reference). NOTE: the running review-api still uses Alembic 0001_initial (github_accounts/sessions/reviews/settings/ai_keys).
-- Converging Alembic onto this schema is a follow-up (see README "Contract drift").
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE user_role       AS ENUM ('user', 'admin');
CREATE TYPE review_depth    AS ENUM ('quick', 'brief', 'deep');
CREATE TYPE review_state    AS ENUM ('queued','running','paused','stuck','retrying','completed','failed','cancelled');
CREATE TYPE provider_kind   AS ENUM ('onprem', 'cloud');
CREATE TYPE attempt_outcome AS ENUM ('success', 'stuck', 'error', 'failed', 'cancelled');

CREATE TABLE users (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    github_id    bigint      NOT NULL UNIQUE,
    login        text        NOT NULL,
    email        text,
    avatar_url   text,
    role         user_role   NOT NULL DEFAULT 'user',
    gh_token_enc bytea,                       -- AES-256-GCM, MASTER_KEY from K8s Secret
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    id         text PRIMARY KEY,              -- sha256(cookie token), never the raw token
    user_id    uuid        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at timestamptz NOT NULL,
    ip         inet,
    user_agent text,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sessions_user_idx    ON sessions(user_id);
CREATE INDEX sessions_expires_idx ON sessions(expires_at) WHERE revoked_at IS NULL;

CREATE TABLE ai_providers (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text          NOT NULL,
    kind        provider_kind NOT NULL,
    base_url    text,
    model       text          NOT NULL,
    api_key_enc bytea,
    key_nonce   bytea,
    key_last4   char(4),
    enabled     boolean       NOT NULL DEFAULT true,
    created_by  uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at  timestamptz   NOT NULL DEFAULT now(),
    updated_at  timestamptz   NOT NULL DEFAULT now()
);

CREATE TABLE reviews (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    repo_url       text         NOT NULL CHECK (repo_url ~ '^https://github\.com/[^/]+/[^/]+$'),
    repo_private   boolean      NOT NULL DEFAULT false,
    ref            text         NOT NULL DEFAULT 'HEAD',
    commit_sha     text CHECK (commit_sha ~ '^[0-9a-f]{40}$'),
    depth          review_depth NOT NULL,
    provider_id    uuid REFERENCES ai_providers(id) ON DELETE SET NULL,
    state          review_state NOT NULL DEFAULT 'queued',
    attempt        int          NOT NULL DEFAULT 1 CHECK (attempt BETWEEN 1 AND 5),
    budget_no      int          NOT NULL DEFAULT 1 CHECK (budget_no >= 1),  -- +1 per POST /reviews/{id}/retry
    files_done     int          NOT NULL DEFAULT 0,
    files_total    int,
    findings_count int          NOT NULL DEFAULT 0,
    summary        jsonb        NOT NULL DEFAULT '{}'::jsonb,   -- severity counts
    findings       jsonb,                                       -- optional cached Finding[] (report.json is source of truth)
    failure_reason text,
    report_path    text,
    last_heartbeat timestamptz,
    created_at     timestamptz  NOT NULL DEFAULT now(),
    updated_at     timestamptz  NOT NULL DEFAULT now(),
    started_at     timestamptz,
    finished_at    timestamptz,
    paused_at      timestamptz
);
CREATE INDEX reviews_user_created_idx ON reviews(user_id, created_at DESC);
CREATE INDEX reviews_state_idx        ON reviews(state);
-- one active review per repo+sha
CREATE UNIQUE INDEX reviews_active_uniq ON reviews(repo_url, commit_sha)
    WHERE state IN ('queued','running','paused','stuck','retrying');

CREATE TABLE review_attempts (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id  uuid NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    budget_no  int  NOT NULL DEFAULT 1 CHECK (budget_no >= 1),
    n          int  NOT NULL CHECK (n BETWEEN 1 AND 5),
    job_name   text,
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at   timestamptz,
    outcome    attempt_outcome,
    error      text,
    UNIQUE (review_id, budget_no, n)
);

CREATE TABLE settings (
    key        text PRIMARY KEY,
    value      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    updated_by uuid REFERENCES users(id) ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE webhook_deliveries (
    delivery_id text PRIMARY KEY,             -- X-GitHub-Delivery (dedupe)
    event       text        NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    status      text        NOT NULL DEFAULT 'accepted'
);

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END $$ LANGUAGE plpgsql;
CREATE TRIGGER users_updated        BEFORE UPDATE ON users        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER reviews_updated      BEFORE UPDATE ON reviews      FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER ai_providers_updated BEFORE UPDATE ON ai_providers FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER settings_updated     BEFORE UPDATE ON settings     FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- First user ever becomes admin (role in user|admin is enforced by the user_role enum).
CREATE OR REPLACE FUNCTION first_user_admin() RETURNS trigger AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM users) THEN NEW.role = 'admin'; END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER users_first_admin BEFORE INSERT ON users FOR EACH ROW EXECUTE FUNCTION first_user_admin();
