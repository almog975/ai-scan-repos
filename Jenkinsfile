// AI Scan Repos monorepo pipeline. Images: <registry>/ai-scan/<svc>:<git-sha> (never 'latest').
// Layout: deploy/{frontend,gateway,review-api,postgres,scanner-worker,platform}/{base,overlays/{local,k3s,eks}}.
// Each built service maps 1:1 to its own deploy folder (postgres/platform have no image built here).
def SERVICES = [ // svc : [source folder, deploy folder]
  'frontend'      : ['frontend',       'frontend'],
  'gateway'       : ['gateway',        'gateway'],
  'review-api'    : ['review-api',     'review-api'],
  'scanner-worker': ['scanner-worker', 'scanner-worker'],
]
// Registry host per TARGET; must match the newName / SCANNER_IMAGE host in deploy/*/overlays/<target>.
def REGISTRY_HOSTS = [
  'local': 'registry.ai-scan.local',
  'k3s'  : 'registry.ai-scan.127.0.0.1.nip.io',
  'eks'  : 'registry.ai-scan.example.com',
]
def changed = []
// Services with their own image stage (build+Trivy+push); excluded from the generic Build/Trivy/Push stages.
def OWN_STAGE = ['scanner-worker', 'frontend']
def skipBuild = false
pipeline {
  agent any
  parameters { choice(name: 'TARGET', choices: ['k3s', 'local', 'eks'], description: 'Overlay to update (default k3s)') }
  environment {
  }
  options { timestamps(); disableConcurrentBuilds() }
  triggers { githubPush() }  // via smee.io relay, see docs/ci.md
  stages {
    stage('Loop guard') { steps { script {
      env.REGISTRY = REGISTRY_HOSTS[params.TARGET]   // images are built/pushed to the TARGET's registry
      echo "TARGET=${params.TARGET} REGISTRY=${env.REGISTRY}"
      def msg = sh(script: 'git log -1 --pretty=%B', returnStdout: true).trim()
      def base = env.GIT_PREVIOUS_SUCCESSFUL_COMMIT ?: 'HEAD~1'
      def files = sh(script: "git diff --name-only ${base} HEAD || true", returnStdout: true).trim().split('\n').findAll { it }
      if (msg.contains('[skip ci]')) {
        skipBuild = true; echo 'Last commit has [skip ci]; skipping.'
      } else if (files && files.every { it.startsWith("deploy/") }) {
        skipBuild = true; echo 'Only deploy/ changed; skipping.'
      }
      if (skipBuild) { currentBuild.result = 'NOT_BUILT' }
    } } }
    stage('Detect changes') { when { expression { !skipBuild } } steps { script {
      env.GIT_SHA = sh(script: 'git rev-parse --short HEAD', returnStdout: true).trim()
      def base = env.GIT_PREVIOUS_SUCCESSFUL_COMMIT ?: 'HEAD~1'
      def files = sh(script: "git diff --name-only ${base} HEAD || true", returnStdout: true).trim().split('\n')
      changed = SERVICES.keySet().findAll { s -> files.any { it.startsWith("${SERVICES[s][0]}/") } } as List
      echo "Changed services: ${changed}"
    } } }
    stage('Install CI deps') { when { expression { !skipBuild } } steps {
      // venv on the agent; later stages put it first on PATH
      sh 'python3 -m venv .ci-venv && .ci-venv/bin/pip install -q --upgrade pip && .ci-venv/bin/pip install -q -r requirements-ci.txt -r gateway/requirements.txt'
      // semgrep pins rich~=13.5 which conflicts with schemathesis (rich>=13.9.4): worker gets its own venv
      sh 'python3 -m venv .ci-venv-worker && .ci-venv-worker/bin/pip install -q -r scanner-worker/requirements.txt pytest==9.2.0 jsonschema[format]==4.26.0 referencing==0.37.0 PyYAML==6.0.2 respx==0.22.0'
    } }
    stage('Contracts') { when { expression { !skipBuild } } steps {
      sh 'export PATH="$PWD/.ci-venv/bin:$PATH" && make contracts && python3 -m pytest -q contracts/tests'
    } }
    stage('Test') { when { expression { !skipBuild && (changed - ['frontend']) } } steps { script {
      (changed - ['frontend']).each { s -> dir(SERVICES[s][0]) { sh "export AISCAN_WORKER_PYTHON=\$WORKSPACE/.ci-venv-worker/bin/python PATH=\"\$WORKSPACE/${s == 'scanner-worker' ? '.ci-venv-worker' : '.ci-venv'}/bin:\$PATH\" && make test" } }
    } } }
    stage('DB parity (Postgres)') { when { expression { !skipBuild } } steps {
      sh 'export PATH="$PWD/.ci-venv/bin:$PATH" && ci/db-parity.sh'   // alembic head == infra/db/schema.sql via pg_dump, plus downgrade/upgrade round trip
    } }
    stage('Schemathesis') { when { expression { !skipBuild } } steps {
      sh 'export PATH="$PWD/.ci-venv/bin:$PATH" && ci/schemathesis.sh 50'   // review-api vs contracts/review-api.openapi.yaml
    } }
    stage('Build') { when { expression { !skipBuild && (changed - OWN_STAGE) } } steps { script {
      (changed - OWN_STAGE).each { s -> sh "docker build -t ${env.REGISTRY}/ai-scan/${s}:${env.GIT_SHA} ${SERVICES[s][0]}" }
    } } }
    stage('Trivy scan') { when { expression { !skipBuild && (changed - OWN_STAGE) } } steps { script {
      (changed - OWN_STAGE).each { s -> sh "trivy image --exit-code 1 --severity HIGH,CRITICAL --no-progress ${env.REGISTRY}/ai-scan/${s}:${env.GIT_SHA}" }
    } } }
    // Phase 2: scanner-worker image (scanner-worker/Dockerfile, context scanner-worker/). Path filter = the
    // git-diff based 'changed' list only: `changeset` is empty on a branch's first build and would skip the image.
    // Deploy (below) writes SCANNER_IMAGE into the scanner-image configMapGenerator.
    stage('Scanner worker image') {
      when { expression { !skipBuild && changed.contains('scanner-worker') } }
      steps { script {
        def img = "${env.REGISTRY}/ai-scan/scanner-worker:${env.GIT_SHA}"
        sh "docker build -t ${img} -f scanner-worker/Dockerfile scanner-worker"
        sh "trivy image --exit-code 1 --severity HIGH,CRITICAL --no-progress ${img}"
        if (env.BRANCH_NAME == 'main') {
          withCredentials([usernamePassword(credentialsId: 'registry-creds', usernameVariable: 'U', passwordVariable: 'P')]) {
            sh 'echo "$P" | docker login "$REGISTRY" -u "$U" --password-stdin'
            sh "docker push ${img}"
          }
        }
      } }
    }
    // Phase 4: frontend (frontend/Dockerfile: node:20 build -> nginxinc/nginx-unprivileged, port 8080).
    // Same 'changed'-list path filter as the scanner-worker stage. npm ci needs frontend/package-lock.json.
    // Agent needs node >= 20 + npm; vitest uses jsdom (no browser). CI=true keeps vitest/npm non-interactive.
    // Deploy (below) runs kustomize edit set image ai-scan/frontend=... as before.
    stage('Frontend image') {
      when { expression { !skipBuild && changed.contains('frontend') } }
      steps { script {
        def img = "${env.REGISTRY}/ai-scan/frontend:${env.GIT_SHA}"
        dir('frontend') {
          sh 'node --version && npm --version'
          sh 'CI=true npm ci --no-audit --no-fund'
          try { sh 'CI=true npm test -- --reporter=default --reporter=junit --outputFile.junit=reports/vitest-junit.xml' }
          finally { junit allowEmptyResults: false, testResults: 'reports/vitest-junit.xml' }
          sh 'CI=true npm run build'
        }
        sh "docker build -t ${img} -f frontend/Dockerfile frontend"
        sh "trivy image --exit-code 1 --severity HIGH,CRITICAL --no-progress ${img}"
        if (env.BRANCH_NAME == 'main') {
          withCredentials([usernamePassword(credentialsId: 'registry-creds', usernameVariable: 'U', passwordVariable: 'P')]) {
            sh 'echo "$P" | docker login "$REGISTRY" -u "$U" --password-stdin'
            sh "docker push ${img}"
          }
        }
      } }
    }
    stage('Push') { when { allOf { branch 'main'; expression { !skipBuild && (changed - OWN_STAGE) } } } steps {
      withCredentials([usernamePassword(credentialsId: 'registry-creds', usernameVariable: 'U', passwordVariable: 'P')]) { script {
        sh 'echo "$P" | docker login "$REGISTRY" -u "$U" --password-stdin'
        (changed - OWN_STAGE).each { s -> sh "docker push ${env.REGISTRY}/ai-scan/${s}:${env.GIT_SHA}" }
      } }
    } }
    stage('Deploy') { when { allOf { branch 'main'; expression { !skipBuild && changed } } } steps { script {
      changed.each { s ->
        def img = "${env.REGISTRY}/ai-scan/${s}:${env.GIT_SHA}"
        dir("deploy/${SERVICES[s][1]}/overlays/${params.TARGET}") {
          if (s != 'scanner-worker') {
            // frontend, gateway, review-api: images[] name ai-scan/<svc> -> newName <host>/ai-scan/<svc>, newTag <sha>
            sh "kustomize edit set image ai-scan/${s}=${img}"
          } else {
            // scanner-worker: review-api reads SCANNER_IMAGE (configMapKeyRef) from the 'scanner-image'
            // configMapGenerator in this overlay. Preferred: kustomize edit set configmap (kustomize >= v5.1).
            // Fallbacks for older agents: yq (mikefarah v4), then sed on the SCANNER_IMAGE= literal.
            sh """
              set -e
              if kustomize edit set configmap --help >/dev/null 2>&1; then
                kustomize edit set configmap scanner-image --from-literal=SCANNER_IMAGE=${img}
              elif command -v yq >/dev/null 2>&1; then
                yq -i '(.configMapGenerator[] | select(.name == "scanner-image") | .literals) |= map(select(test("^SCANNER_IMAGE=") | not)) + ["SCANNER_IMAGE=${img}"]' kustomization.yaml
              else
                sed -i -E 's#(SCANNER_IMAGE=)[^"[:space:],]*#\\1${img}#' kustomization.yaml
              fi
            """
          }
        }
      }
      sh """
        git config user.name jenkins && git config user.email jenkins@ci
        git add deploy/*/overlays/${params.TARGET}/kustomization.yaml
        git diff --cached --quiet || git commit -m "ci(${params.TARGET}): ${changed.join(',')} -> ${env.GIT_SHA} [skip ci]"
      """
      // ---------------------------------------------------------------------------
      // TODO(push): enable once the GitOps branch name is agreed. Stub only.
      // withCredentials([usernamePassword(credentialsId: 'github-bot', usernameVariable: 'GU', passwordVariable: 'GT')]) {
      //   sh 'git push "https://${GU}:${GT}@github.com/almog975/ai-scan-repos.git" HEAD:<BRANCH>'
      // }
      // ---------------------------------------------------------------------------
    } } }
  }
}
