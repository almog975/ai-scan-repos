# AI Scan Repos – User Guide

This guide is for people who use the web UI to run security reviews of GitHub repositories. Operators should read the [runbook](runbooks/runbook.md). Admins who manage AI providers should read [admin: AI providers](runbooks/admin-ai-providers.md).

> **Screenshots:** the images come from the real frontend (`frontend/`, built with Vite) running in a headless browser, with **mocked API responses**. The repositories, numbers and providers in them are sample data.
> TODO: re-capture them against a live k3s deployment.

Each step below names the API call behind it (gateway `/api/*` → review-api). That lets you check results with `curl` too.

---

## 1. Log in

![Login](images/user-guide/login.png)

1. Open the app's URL. If you aren't logged in, you'll see **AI Scan Repos** and a **Log in with GitHub** button.
2. Click it. You're sent to GitHub OAuth (`GET /auth/github/login`, scopes `read:user repo`). Approve the app.
3. GitHub sends you back to `/auth/github/callback`. The gateway sets a session cookie and a CSRF cookie (`aiscan_csrf`), then sends you to `/`.
4. Your GitHub login (`@<login>`) appears in the header, next to **Log out** (`POST /auth/logout`).

Notes:
- If the deployment has an allow-list and your GitHub user isn't on it, login fails with **403 "GitHub user not in allow-list"**. Ask an admin to add you.
- The first user ever to log in becomes `admin`. Everyone after that is a `user`, unless they're listed in the admin list.
- Sessions expire after `SESSION_TTL_HOURS`. After that, log in again.

## 2. Run a scan

![New review](images/user-guide/reviews.png)

The **Reviews** page has a **New review** form at the top and your reviews below it.

1. **GitHub repository URL**: paste `https://github.com/<owner>/<repo>`. A trailing `.git` or `/` is fine. Any other format shows *"Must be https://github.com/<owner>/<repo>"*, and **Start review** stays disabled.
2. **Depth**: choose Quick, Brief or Deep (see §3). The default is Quick.
3. **AI provider**: **No AI (scanners only)** is the default. You can also pick any enabled provider from `GET /api/ai-providers`.
4. Click **Start review**. This sends `POST /api/reviews` with `{repo_url, depth, provider_id, ack_cloud}`, and the new review appears at the top of the list.

The UI has no field for the branch or ref. The API accepts `ref` (TBD in the UI), so the UI always scans the default ref.

Common errors (the message from the API is shown in red under the form):
- **409**: an active review already exists for the same repository and commit.
- **422 CLOUD_ACK_REQUIRED**: you chose a cloud provider without confirming (see §3).
- A capacity error: too many reviews are running. Try again later.

## 3. Choose depth and AI provider

![Cloud provider warning](images/user-guide/new-review-cloud.png)

| Depth | What runs | What goes to the AI |
|---|---|---|
| **Quick** | Semgrep, Gitleaks and Trivy | Nothing beyond the scanners' summary |
| **Brief** | Same scanners | The **top 10 findings**, with code context. The AI fills `ai_fix`, `confidence` and `ai_severity` |
| **Deep** | Same scanners, plus an AI review of each file (up to `DEEP_MAX_FILES`, 40 by default) | Whole files and their findings. The AI fills `ai_fix`, `confidence` and `ai_severity` |

If you choose **No AI**, only the scanners run, whatever depth you pick.

**Cloud providers.** Each provider is either `onprem` or `cloud`; an admin sets this. If you pick a cloud provider (for example Grok at `api.x.ai`), a yellow box appears: *"⚠️ Cloud AI selected. Source code snippets and scanner findings … will be sent to a third-party AI service outside your network."* You must tick **I understand and agree to send code to <provider>** before **Start review** is enabled. The UI then sends `ack_cloud: true`. The API rejects a cloud request without it (422 `CLOUD_ACK_REQUIRED`). A report produced with a cloud provider carries a cloud banner (§6).

## 4. Watch progress

![Reviews list](images/user-guide/reviews.png)

The table has these columns: Repository, Depth, AI (`onprem`, `cloud` or `—`), Status, Progress (files done / files total, as %), Attempts, and the actions.

While any review is queued, running, retrying or stuck, the list refreshes itself every 3 seconds (`GET /api/reviews`).

The API also has detailed progress endpoints the UI doesn't use yet (UI TBD):
- `GET /api/reviews/{id}/progress`, a Server-Sent Events stream (`?format=json` for one snapshot)
- `GET /api/reviews/{id}/files`, the status of each file
- `GET /api/reviews/{id}/attempts`, the attempt history

### Review states

| State | Meaning | What you can do |
|---|---|---|
| `queued` | Created. Waiting for the scanner Job to send its first heartbeat | Cancel |
| `running` | The scanner is working. Progress goes up | Pause, Cancel |
| `paused` | You paused it. The checkpoint is kept | Resume |
| `stuck` | No heartbeat for 2 checks in a row past the threshold | Resume (retries now) or wait for the automatic retry |
| `retrying` | A new attempt is starting. At most 5 attempts per budget | Cancel |
| `completed` | Finished | Report |
| `failed` | The attempts ran out, or there was a fatal error. The reason is shown next to the badge | Retry |
| `cancelled` | Cancelled by the user | — |

The full diagram is in [review-state-machine.md](diagrams/review-state-machine.md).

## 5. Pause and resume

- **Pause** (`POST /api/reviews/{id}/pause`) works only on running reviews. The scanner saves a checkpoint after every file, so no finished work is lost.
- **Resume** (`POST /api/reviews/{id}/resume`) shows up for paused and stuck reviews.
  - A paused review goes back to `queued` and continues from its checkpoint. This doesn't use up an attempt.
  - A stuck review goes to `retrying` right away, which starts a new attempt.
  - Before resuming, the review's AI provider is checked again. If it has been disabled or deleted, you get a 422.
- **Cancel** (`POST /api/reviews/{id}/cancel`) works through the API only (UI TBD). Any partial results are kept.
- **Retry a failed review** (`POST /api/reviews/{id}/retry`) also works through the API only (UI TBD). It opens a new attempt budget.

You can pause a review and resume it days later. See [resume-after-days.md](runbooks/resume-after-days.md).

## 6. Read and download the report

![Report](images/user-guide/report.png)

When a review is `completed`, click **Report**. The page shows:
- If a cloud provider was used, a banner at the top: *"⚠️ Code from this repository was sent to cloud AI (<name>)."*
- A count for each severity (critical / high / medium / low / info).
- The rendered `report.md`. The structure is described in [report-md-spec.md](report-md-spec.md).

**Download:** the UI has no download button yet (TBD). To get the file, use the API with your session cookie:

```bash
curl -b "<session-cookie>=<value>" -o report.md  https://<host>/api/reviews/<id>/report.md
curl -b "<session-cookie>=<value>" -o report.json https://<host>/api/reviews/<id>/report.json
```

Or open `https://<host>/api/reviews/<id>/report.md` in the browser while you're logged in.

Report API responses:
- **200**: the report.
- **409**: the review isn't completed yet. For a `failed` or `cancelled` review with no partial report, you also get 409. If a partial report exists, it is returned (`report.partial.*`).
- **410 REPORT_EXPIRED**: retention removed the files (`REPORT_RETENTION_DAYS`).

## 7. Known gaps / TBD in the UI

- There's no Cancel or Retry button. Use the API.
- There's no download button for report.md or report.json. Use the API or open the URL directly.
- There's no branch/ref field, no per-file progress view and no attempts view. The API supports all of these.
- **Pause is shown for `stuck` reviews**, but the API only lets you pause queued, running and retrying ones, so clicking it returns 409.
- **Resume never sends `ack_cloud`.** Resuming a review that uses a cloud provider fails with 422 `CLOUD_ACK_REQUIRED`. Workaround: `POST /api/reviews/{id}/resume` with `{"ack_cloud": true}`.
- **Errors from Pause and Resume aren't caught**, so nothing appears on screen when they fail.
- **The Report button only appears for `completed` reviews**, so you can't reach partial reports of failed or cancelled reviews in the UI.
