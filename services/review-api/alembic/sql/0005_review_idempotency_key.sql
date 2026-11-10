-- Phase 9 (webhook reclaim guard): POST /reviews Idempotency-Key. Applied by Alembic 0005.
ALTER TABLE reviews ADD COLUMN idempotency_key text;
ALTER TABLE reviews ADD CONSTRAINT reviews_idempotency_key_key UNIQUE (idempotency_key);  -- NULLs never collide
