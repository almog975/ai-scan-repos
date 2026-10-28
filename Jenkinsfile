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
    stage('Test') { when { expression { !skipBuild && changed } } steps { script {
      changed.each { s -> dir(SERVICES[s][0]) { sh 'make test' } }
    } } }
    stage('Build') { when { expression { !skipBuild && changed } } steps { script {
      changed.each { s -> sh "docker build -t ${env.REGISTRY}/ai-scan/${s}:${env.GIT_SHA} ${SERVICES[s][0]}" }
    } } }
    stage('Trivy scan') { when { expression { !skipBuild && changed } } steps { script {
      changed.each { s -> sh "trivy image --exit-code 1 --severity HIGH,CRITICAL --no-progress ${env.REGISTRY}/ai-scan/${s}:${env.GIT_SHA}" }
    } } }
    stage('Push') { when { allOf { branch 'main'; expression { !skipBuild && changed } } } steps {
      withCredentials([usernamePassword(credentialsId: 'registry-creds', usernameVariable: 'U', passwordVariable: 'P')]) { script {
        sh 'echo "$P" | docker login "$REGISTRY" -u "$U" --password-stdin'
        changed.each { s -> sh "docker push ${env.REGISTRY}/ai-scan/${s}:${env.GIT_SHA}" }
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
