-- Phase 4 (admin AI providers): cloud data-egress acknowledgement. Applied by Alembic 0004.
ALTER TABLE ai_providers ADD COLUMN cloud_ack_at timestamptz;
ALTER TABLE ai_providers ADD COLUMN cloud_ack_by uuid REFERENCES users(id) ON DELETE SET NULL;
UPDATE ai_providers SET cloud_ack_at = created_at, cloud_ack_by = created_by WHERE kind = 'cloud';  -- pre-existing cloud providers were created through the per-review warning flow
