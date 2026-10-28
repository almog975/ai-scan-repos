# Admin guide — adding an AI provider

Based on the code on `feat/phase-1-backend` (review-api `app/routers/providers.py`, `app/providers/`,
`app/crypto.py`, `app/jobs.py`, `app/launcher.py`; scanner-worker `scanner/ai_client.py`, `scanner/run.py`)
and the contract `contracts/review-api.openapi.yaml`. Planner decisions for Phase 2 are described as the intended behavior; where the code on
`feat/phase-2-scanner` doesn't do it yet it is marked **decided, implementation pending (Phase 2)**. Other gaps are marked **TODO**.

> ⚠️ **Cloud providers send your source code outside the network.** With a provider of kind `cloud`,
> the scanner Job sends scanner findings **and source-code snippets / whole files** (Brief: code context
> around the top 10 findings; Deep: each code file) to a third-party API. Use a cloud provider only for
> repositories whose code may leave your organisation. Prefer an on-prem provider (below) for private code.

## 1. Who can do this
Only users with `role = admin` (`require_admin`; non-admins get 403). The first user to log in becomes admin
(see [contracts/README.md](../../contracts/README.md)). Admin routes are reached through the gateway at
`/api/admin/...` with the session cookie (gateway `app/routes.py` allow-list).

**UI:** the admin AI-providers screen is planned for **Phase 4**. Until then use the API. (The Phase 2 frontend only reads `GET /api/ai-providers` and sends `provider_id` + `ack_cloud`; it has no key entry.)

## 2. API

| Method | Path (via gateway) | Purpose |
|---|---|---|
| GET | `/api/admin/ai-providers` | list all providers; key shown only as `key_masked` (`****abcd`) |
| POST | `/api/admin/ai-providers` | create (201) |
| PATCH | `/api/admin/ai-providers/{id}` | change fields; `api_key: null` clears the key |
| DELETE | `/api/admin/ai-providers/{id}` | delete (204) |
| POST | `/api/admin/ai-providers/{id}/test` | configuration check (502 on failure) |
| GET/PUT | `/api/admin/settings` | `default_provider_id`, `max_concurrent_reviews`, `max_repo_size_mb` |
| GET | `/api/ai-providers` | any user: enabled providers with `cloud_warning` (no secrets) |

Create body (`AIProviderWrite`, unknown fields rejected):

| Field | Required | Notes |
|---|---|---|
| `name` | yes | display name |
| `kind` | yes | `onprem` or `cloud` — drives `cloud_warning` and the `ack_cloud` check |
| `base_url` | no* | must start with `http://` or `https://`. *The **adapter is chosen from the host** of `base_url` (below); with no `base_url` a `cloud` provider becomes `openai` and an `onprem` one becomes `ollama` (`http://ollama:11434`). Decided: adapters normalize `base_url` themselves (e.g. add `/v1`), so the bare host is enough — **decided, implementation pending (Phase 2)**; until then use the exact URLs in the table. |
| `model` | yes | e.g. `qwen2.5-coder:14b`, `gpt-4o-mini` |
| `api_key` | no | encrypted at rest; required by all cloud adapters for `/test` |
| `enabled` | no (default `true`) | disabled providers are hidden from users |

```bash
curl -s -b "$COOKIES" -H 'Content-Type: application/json' -X POST https://<host>/api/admin/ai-providers \
  -d '{"name":"Ollama (GPU box)","kind":"onprem","base_url":"http://gpu-box.tailnet-xyz.ts.net:11434/v1","model":"qwen2.5-coder:14b"}'
curl -s -b "$COOKIES" -X POST https://<host>/api/admin/ai-providers/<id>/test
curl -s -b "$COOKIES" -H 'Content-Type: application/json' -X PUT https://<host>/api/admin/settings \
  -d '{"default_provider_id":"<id>","max_concurrent_reviews":3,"max_repo_size_mb":500}'
```
(`$COOKIES` = your browser session cookies. State-changing calls through the gateway also need the `X-CSRF-Token` header — see `contracts/gateway.openapi.yaml`.)

**`/test` (decided):** makes a real network call to the provider with the stored key and model.
Code today: still a stub that only checks a key/base URL is present, no network call (`app/providers/base.py`) —
**decided, implementation pending (Phase 2)**. Until then verify connectivity yourself (section 3, step 5).

### Adapter selection (by `base_url` host, `app/providers/__init__.py`)

| Host contains | Adapter | Default `kind` to choose | `base_url` to use today (worker appends the path) |
|---|---|---|---|
| `:11434` | `ollama` | on-prem | `http://<host>:11434/v1` (worker calls `/chat/completions`) |
| `api.openai.com` | `openai` | cloud | `https://api.openai.com/v1` |
| `api.anthropic.com` | `anthropic` | cloud | `https://api.anthropic.com/v1` (worker calls `/messages`) |
| `generativelanguage.googleapis.com` | `gemini` | cloud | `https://generativelanguage.googleapis.com/v1beta` |
| `api.groq.com` | `groq` | cloud | `https://api.groq.com/openai/v1` |
| `api.mistral.ai` | `mistral` | cloud | `https://api.mistral.ai/v1` |
| `.openai.azure.com` | `azure_openai` | cloud | your Azure deployment URL (TODO: Azure `api-version`/`api-key` header not verified in worker) |
| anything else (vLLM, LM Studio, **x.ai**) | `openai_compatible` | on-prem | `http://<host>:<port>/v1` |

## 3. On-prem provider over Tailscale (recommended)

An OpenAI-compatible server (Ollama, vLLM, LM Studio) on a GPU machine reached privately over Tailscale —
code never leaves your tailnet.

1. **Model server** (GPU host):
   - Ollama: `OLLAMA_HOST=0.0.0.0:11434 ollama serve` and `ollama pull qwen2.5-coder:14b`. API: `http://<host>:11434/v1`.
   - vLLM: `vllm serve <model> --host 0.0.0.0 --port 8000`. API: `http://<host>:8000/v1`.
   - Bind only to the Tailscale interface or firewall the port from the LAN/internet.
2. **Tailscale on both sides:** install Tailscale on the GPU host (`tailscale up`) and give the cluster
   access to the tailnet — either Tailscale on the K3s node(s) or the Tailscale Kubernetes operator /
   subnet router. Owned by **Infra** (Tailscale manifests/runbook in `infra/`, not yet present).
3. **Address:** use the MagicDNS name (`gpu-box.<tailnet>.ts.net`) or the `100.x.y.z` address
   (`tailscale ip -4` on the GPU host). MagicDNS from pods requires the node/cluster DNS to forward
   `*.ts.net` to `100.100.100.100`; if it doesn't, use the `100.x` IP.
4. **ACLs** (Tailscale admin console → Access controls): allow only the cluster's tag to reach the model port, e.g.
   ```json
   {"tagOwners": {"tag:ai-scan-k3s": ["autogroup:admin"], "tag:gpu": ["autogroup:admin"]},
    "acls": [{"action": "accept", "src": ["tag:ai-scan-k3s"], "dst": ["tag:gpu:11434"]}]}
   ```
5. **Check from the cluster** (same namespace as the scanner Jobs):
   ```bash
   kubectl -n ai-scan-repos run ai-check --rm -it --restart=Never --image=curlimages/curl -- \
     curl -sS http://100.x.y.z:11434/v1/models
   kubectl -n ai-scan-repos run ai-check --rm -it --restart=Never --image=curlimages/curl -- \
     curl -sS http://100.x.y.z:11434/v1/chat/completions -H 'Content-Type: application/json' \
     -d '{"model":"qwen2.5-coder:14b","messages":[{"role":"user","content":"ping"}]}'
   ```
   Egress NetworkPolicy from scanner Jobs to the AI endpoint is owned by **Infra** (not yet present).
6. Create the provider with `kind: "onprem"` and that `base_url` (section 2), then set it as default.

## 4. Cloud providers
Create with `kind: "cloud"`, the `base_url` from the adapter table and an `api_key`. Users then see
`cloud_warning: true` in `GET /api/ai-providers` and in review responses.

How the product warns (as implemented):
- **ack_cloud:** `POST /reviews` with a `cloud` provider and without `"ack_cloud": true` → **422
  `CLOUD_ACK_REQUIRED`** ("code will leave the cluster", `app/routers/reviews.py`). The UI must show the
  warning and resend with `ack_cloud: true`. The Phase 2 frontend sends `provider_id` + `ack_cloud` (commit ab209fd); the exact confirm UX is not verified here.
- **Report:** `report.json` → `provider.kind` (`onprem`/`cloud`); `report.md` header shows
  "⚠️ cloud AI — code/findings were sent outside your network" (`scanner/report.py`).
- **Decided:** cloud vs on-prem is the provider `kind` stored in the DB, everywhere (ack check, `cloud_warning`,
  report banner). Code today: review-api uses the DB `kind`, but the worker still derives the report's `kind`
  from the adapter name, so a cloud API with no matching adapter (e.g. **x.ai/Grok** → `openai_compatible`)
  is reported as on-prem — **decided, implementation pending (Phase 2)**. Until then, check such reports manually.

## 5. Key storage and rotation
- Provider `api_key` is encrypted with **AES-256-GCM** (`app/crypto.py`): key `AI_KEYS_ENCRYPTION_KEY`
  from Secret `ai-keys-master` (32 bytes, hex or base64), random 96-bit nonce per encryption, AAD = provider id.
  Only `key_last4` is shown (`****abcd`); the key is never returned by the API.
- At Job launch review-api decrypts the key and puts it in a **per-review Secret** (`<job>-ai`, label
  `ai-scan-repos/per-review-secret`) consumed via `envFrom` as `AI_API_KEY` — never in argv; the Secret is
  deleted by the launcher's cleanup, with ownerReference GC as fallback (`app/launcher.py`). The worker never writes the key to the volume.
- **Rotate a provider key:** `PATCH /api/admin/ai-providers/{id}` with `{"api_key":"<new>"}`; new Jobs use it.
- **Rotate the master key:** in the **backlog** — see [ADR-0003](../adr/0003-ai-key-rotation.md) (Deferred).
  Changing `ai-keys-master` makes stored keys undecryptable. Interim: create the new secret, restart
  review-api, then re-enter every provider key with PATCH. See [secrets-runbook.md](../secrets-runbook.md).

## 6. Status summary
| Item | Status |
|---|---|
| Cloud/on-prem from DB `kind` in worker/report | **decided, implementation pending (Phase 2)** |
| Adapters normalize `base_url` | **decided, implementation pending (Phase 2)** |
| `/test` real network call | **decided, implementation pending (Phase 2)** |
| Master-key rotation / re-encryption | backlog — [ADR-0003](../adr/0003-ai-key-rotation.md) |
| Admin UI | Phase 4 |
| Tailscale, egress NetworkPolicy | Infra |
| Azure OpenAI in worker | TODO (unverified) |
