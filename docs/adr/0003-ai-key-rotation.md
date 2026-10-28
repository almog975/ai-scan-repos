# ADR-0003: AI provider key rotation (master key)

- **Status:** Deferred (backlog)
- **Date:** 2026-10-28
- **Deciders:** Planner, almog975

## Context
AI provider API keys are stored in `ai_providers.api_key_enc` encrypted with AES-256-GCM
(`review-api/app/crypto.py`): master key `AI_KEYS_ENCRYPTION_KEY` from Secret `ai-keys-master`
(32 bytes), random 96-bit nonce per key (`key_nonce`), AAD = provider id. There is **one** master key and
no key-version column.

**Current limitation:** if `ai-keys-master` changes, every stored key fails to decrypt. review-api logs
"cannot decrypt AI key for provider …" (`app/jobs.py`) and the scan Job starts without `AI_API_KEY`;
`/admin/ai-providers/{id}/test` returns 502. There is no tool to re-encrypt with a new key.
Rotating an individual provider key already works (`PATCH /admin/ai-providers/{id}` with `api_key`).

## Options for master-key rotation
1. **Offline re-encryption job** — a one-shot command/Job reads `OLD_KEY` and `NEW_KEY`, decrypts each row
   and re-encrypts with a fresh nonce in one transaction, then the Secret is switched. Simple; needs a short
   window where both keys are mounted.
2. **Key versioning (keyring)** — add `key_version` to `ai_providers`; review-api loads a keyring
   (`AI_KEYS_ENCRYPTION_KEYS = {v1, v2}`), encrypts with the newest, decrypts with the row's version, and
   lazily or via a background task re-encrypts old rows; retire the old key when none remain. No downtime;
   needs a migration and contract-neutral code changes.
3. **Envelope encryption with an external KMS** (AWS KMS on EKS, Vault Transit) — per-row data keys wrapped
   by a KMS key; rotation is handled by the KMS. Strongest, but adds a dependency not available on local/K3s.

## Decision
Deferred to the backlog. No option is chosen yet; option 2 is the likely default when picked up.

## Interim workaround
To change the master key today: create the new `ai-keys-master` Secret, restart review-api, then
**re-enter every provider key** with `PATCH /api/admin/ai-providers/{id}` `{"api_key": "<key>"}` (keys are
not recoverable after the switch, so have them at hand first). See
[admin AI providers runbook](../runbooks/admin-ai-providers.md) and [secrets runbook](../secrets-runbook.md).

## Consequences
Master-key rotation needs a manual re-entry of all keys and causes scans without AI until done.
