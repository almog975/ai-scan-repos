-- Phase 3 (recovery): failure codes, stuck/retry/pause/cancel bookkeeping. Applied by Alembic 0003.
CREATE TYPE failure_code AS ENUM ('CLONE_FAILED','REPO_TOO_LARGE','SCANNER_ERROR','AI_PROVIDER_ERROR','AI_RATE_LIMITED','TIMEOUT','MAX_ATTEMPTS_EXCEEDED','INTERNAL');
ALTER TABLE reviews ADD COLUMN failure_code failure_code;
ALTER TABLE reviews ADD COLUMN stuck_since timestamptz;
ALTER TABLE reviews ADD COLUMN next_retry_at timestamptz;
ALTER TABLE reviews ADD COLUMN paused_by uuid REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE reviews ADD COLUMN cancelled_at timestamptz;
ALTER TABLE reviews ADD COLUMN cancelled_by uuid REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE reviews ADD COLUMN overdue_since timestamptz;  -- reconciler: first pass over the heartbeat threshold
