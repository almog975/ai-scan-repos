#!/usr/bin/env bash
# Deploy stage helper (Jenkinsfile, main only): push the tag-bump commit (HEAD) to DEPLOY_BRANCH and open a PR to
# main. Never force-pushes: a re-run whose branch already exists succeeds only if the remote branch has the same tree
# (same tag bump), otherwise it fails; "PR already exists" (422) is treated as success.
# Token handling: GU/GT come from withCredentials(usernamePassword 'github-bot'). The token is handed to git via a
# GIT_ASKPASS script that reads it from the environment (never in the URL, argv, logs or .git/config) and to curl
# via a header file (-H @file, not argv). Tests: ci/tests/test_deploy_pr.py (local bare repo + fake GitHub API).
set +x
set -euo pipefail
: "${GU:?GU (github-bot username) not set}" "${GT:?GT (github-bot token) not set}"
: "${DEPLOY_BRANCH:?}" "${COMPONENTS:?}" "${GIT_SHA:?}" "${TARGET:?}"
case "$DEPLOY_BRANCH" in deploy/*) ;; *) echo "DEPLOY_BRANCH must start with deploy/" >&2; exit 2 ;; esac
GITHUB_REPO="${GITHUB_REPO:-almog975/ai-scan-repos}"
GITHUB_API="${GITHUB_API:-https://api.github.com}"
PUSH_URL="${PUSH_URL:-https://github.com/${GITHUB_REPO}.git}"
BASE_BRANCH="${BASE_BRANCH:-main}"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/askpass" <<'ASK'
#!/bin/sh
case "$1" in Username*) printf '%s\n' "$GU" ;; *) printf '%s\n' "$GT" ;; esac
ASK
chmod 700 "$tmp/askpass"

# 1. push (credential.helper= disables any stored helper; nothing is written to .git/config). No --force: the
#    refspec has no "+", so an existing branch is never rewritten.
agit() { GIT_ASKPASS="$tmp/askpass" GIT_TERMINAL_PROMPT=0 git -c credential.helper= "$@"; }
if agit push "$PUSH_URL" "HEAD:refs/heads/${DEPLOY_BRANCH}"; then
  echo "Pushed $(git rev-parse --short HEAD) to ${DEPLOY_BRANCH}"
else
  # rebuild of the same sha: accept the existing branch only if it carries exactly this bump
  agit fetch -q "$PUSH_URL" "refs/heads/${DEPLOY_BRANCH}" || { echo "push of ${DEPLOY_BRANCH} failed" >&2; exit 1; }
  if [ "$(git rev-parse 'FETCH_HEAD^{tree}')" = "$(git rev-parse 'HEAD^{tree}')" ]; then
    echo "${DEPLOY_BRANCH} already has this tag bump ($(git rev-parse --short FETCH_HEAD)); not pushing"
  else
    echo "${DEPLOY_BRANCH} exists with different content; refusing to overwrite (no force push)" >&2; exit 1
  fi
fi

# 2. PR via REST API
( umask 077; printf 'Authorization: Bearer %s\n' "$GT" > "$tmp/auth" )
api() { # api METHOD PATH [DATAFILE] -> body in $tmp/resp, prints HTTP code
  curl -sS -o "$tmp/resp" -w '%{http_code}' -X "$1" -H @"$tmp/auth" \
    -H 'Accept: application/vnd.github+json' -H 'X-GitHub-Api-Version: 2022-11-28' \
    ${3:+-H 'Content-Type: application/json' --data @"$3"} "${GITHUB_API}$2"
}
python3 - "$tmp/req" <<PY
import json, os, sys
c, sha, t, b = os.environ["COMPONENTS"], os.environ["GIT_SHA"], os.environ["TARGET"], os.environ["DEPLOY_BRANCH"]
body = (f"Automated tag bump by Jenkins ({os.environ.get('BUILD_URL', 'local run')}).\n\n"
        f"- Components: {', '.join(c.split(','))}\n- Image tag (commit sha): \`{sha}\`\n- Overlay: \`deploy/<component>/overlays/{t}\`\n\n"
        "Merge to deploy: Argo CD syncs main. Rollback: revert this PR's merge (docs/runbooks/rollback.md).")
json.dump({"title": f"ci({t}): {c} -> {sha} [skip ci]", "head": b, "base": os.environ.get("BASE_BRANCH", "main"),
           "body": body, "maintainer_can_modify": True}, open(sys.argv[1], "w"))
PY
code="$(api POST "/repos/${GITHUB_REPO}/pulls" "$tmp/req")"
url() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d=d[0] if isinstance(d,list) and d else d; print(d.get("html_url","?") if isinstance(d,dict) else "?")' "$tmp/resp"; }
case "$code" in
  201) echo "Opened PR: $(url)" ;;
  422)
    if grep -q 'already exists' "$tmp/resp"; then
      owner="${GITHUB_REPO%%/*}"
      code="$(api GET "/repos/${GITHUB_REPO}/pulls?state=open&head=${owner}:${DEPLOY_BRANCH}&base=${BASE_BRANCH}")"
      echo "PR already exists (422), branch updated: $(url) [lookup HTTP ${code}]"
    else
      echo "GitHub API 422:" >&2; cat "$tmp/resp" >&2; exit 1
    fi ;;
  *) echo "GitHub API HTTP ${code}:" >&2; cat "$tmp/resp" >&2; exit 1 ;;
esac
