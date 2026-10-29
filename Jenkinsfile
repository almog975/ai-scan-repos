// AI Scan Repos monorepo pipeline (Phase 6). Images: <registry>/ai-scan/<svc>:<git-sha> (never 'latest').
// Runs on the Kubernetes agent pod template 'ai-scan-agent' (deploy/jenkins/values.yaml, JCasC):
//   container 'tools'  : node20 + python3 + trivy + kustomize + crane + git (ci/agent/Dockerfile)
//   container 'kaniko' : image builds without a Docker daemon (no privileged pod on k3s)
// Images are built to a tarball (no push), scanned with trivy --input, then pushed with crane on main only.
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
def BOT_NAME  = 'ai-scan-ci-bot'
def BOT_EMAIL = 'ci-bot@ai-scan.local'
def changed = []
def skipBuild = false

// Component filter: ONE list ('changed') drives Test, Build, Scan, Push and Deploy, so Deploy covers exactly what
// was built. It diffs against GIT_PREVIOUS_SUCCESSFUL_COMMIT (or HEAD~1); if that base is missing (first build,
// force-push, shallow clone) or a shared path changed (SHARED), every component is selected - never an empty list.
def SHARED = ['contracts/', 'mk/', 'Makefile']
def wants = { String svc -> !skipBuild && changed.contains(svc) }
def img = { String svc -> "${env.REGISTRY}/ai-scan/${svc}:${env.GIT_SHA}" }
def tarOf = { String svc -> "${env.WORKSPACE}/.images/${svc}.tar" }

def kanikoBuild = { String svc ->
  container('kaniko') {
    sh """/kaniko/executor --context=dir://${env.WORKSPACE}/${SERVICES[svc][0]} \
          --dockerfile=${env.WORKSPACE}/${SERVICES[svc][0]}/Dockerfile \
          --build-arg=VITE_APP_VERSION=${env.GIT_SHA} \
          --destination=${img(svc)} --no-push --tar-path=${tarOf(svc)} \
          --cleanup --snapshot-mode=redo"""
  }
}
def trivyScan = { String svc ->
  container('tools') {
    sh "trivy image --exit-code 1 --severity CRITICAL,HIGH --no-progress --input ${tarOf(svc)}"
  }
}
def cranePush = { String svc ->
  container('tools') {
    withCredentials([usernamePassword(credentialsId: 'registry-push', usernameVariable: 'U', passwordVariable: 'P')]) {
      sh 'echo "$P" | crane auth login "$REGISTRY" -u "$U" --password-stdin'
      sh "crane push ${tarOf(svc)} ${img(svc)}"
    }
  }
}

pipeline {
  agent { kubernetes { inheritFrom 'ai-scan-agent'; defaultContainer 'tools' } }
  parameters { choice(name: 'TARGET', choices: ['k3s', 'local', 'eks'], description: 'Overlay to update (default k3s)') }
  options { timestamps(); disableConcurrentBuilds(); buildDiscarder(logRotator(numToKeepStr: '30')) }
  triggers {
    githubPush()          // GitHub webhook via the smee-client Deployment (deploy/jenkins/smee)
    pollSCM('H/5 * * * *') // fallback if the smee relay is down
  }
  stages {
    stage('Loop guard') { steps { script {
      env.REGISTRY = REGISTRY_HOSTS[params.TARGET]
      echo "TARGET=${params.TARGET} REGISTRY=${env.REGISTRY}"
      def msg    = sh(script: 'git log -1 --pretty=%B', returnStdout: true).trim()
      def author = sh(script: 'git log -1 --pretty=%ae', returnStdout: true).trim()
      def base   = env.GIT_PREVIOUS_SUCCESSFUL_COMMIT ?: 'HEAD~1'
      def haveBase = sh(script: "git cat-file -e '${base}^{commit}' 2>/dev/null && echo yes || echo no", returnStdout: true).trim() == 'yes'
      def files  = haveBase ? sh(script: "git diff --name-only ${base} HEAD", returnStdout: true).trim().split('\n').findAll { it } : []
      if (!haveBase) { echo "Base ${base} not found; selecting all components." }
      if (author == BOT_EMAIL) {
        skipBuild = true; echo "Last commit authored by ${BOT_NAME}; skipping."
      } else if (msg.contains('[skip ci]')) {
        skipBuild = true; echo 'Last commit has [skip ci]; skipping.'
      } else if (files && files.every { it.startsWith('deploy/') }) {
        skipBuild = true; echo 'Only deploy/** changed; skipping.'
      }
      if (skipBuild) { currentBuild.result = 'NOT_BUILT'; return }
      env.GIT_SHA = sh(script: 'git rev-parse --short HEAD', returnStdout: true).trim()
      def shared = !haveBase || files.any { f -> SHARED.any { f == it || f.startsWith(it) } }
      changed = shared ? (SERVICES.keySet() as List)
                       : (SERVICES.keySet().findAll { s -> files.any { it.startsWith("${SERVICES[s][0]}/") } } as List)
      echo "Changed services: ${changed}"
      sh 'mkdir -p .images reports'
    } } }

    stage('Test') {
      when { expression { !skipBuild } }
      stages {
        stage('Install CI deps') { steps {
          sh 'python3 -m venv .ci-venv && .ci-venv/bin/pip install -q --upgrade pip && .ci-venv/bin/pip install -q -r requirements-ci.txt -r gateway/requirements.txt'
          // semgrep pins rich~=13.5 which conflicts with schemathesis (rich>=13.9.4): worker gets its own venv
          sh 'python3 -m venv .ci-venv-worker && .ci-venv-worker/bin/pip install -q -r scanner-worker/requirements.txt pytest==9.2.0 jsonschema[format]==4.26.0 referencing==0.37.0 PyYAML==6.0.2 respx==0.22.0'
        } }
        stage('Contracts') { steps {
          sh 'export PATH="$PWD/.ci-venv/bin:$PATH" && make contracts && python3 -m pytest -q contracts/tests --junitxml=reports/contracts-junit.xml'
          // Pipeline/manifest checks (ci/tests): Jenkinsfile gates, K3s overlays, make exit codes, db-parity drift (PGHOST sidecar).
          sh 'export PATH="$PWD/.ci-venv/bin:$PATH" && python3 -m pytest -q ci/tests --junitxml=reports/ci-junit.xml'
        } }
        stage('frontend') {
          when { expression { wants('frontend') } }
          steps { dir('frontend') {
            sh 'CI=true npm ci --no-audit --no-fund'
            sh 'CI=true npm test -- --reporter=default --reporter=junit --outputFile.junit=$WORKSPACE/reports/frontend-junit.xml'
            sh 'CI=true npm run build'
          } }
        }
        stage('gateway') {
          when { expression { wants('gateway') } }
          steps { dir('gateway') { sh 'PATH="$WORKSPACE/.ci-venv/bin:$PATH" PYTEST_ADDOPTS="--junitxml=$WORKSPACE/reports/gateway-junit.xml" make test' } }
        }
        stage('review-api') {
          when { expression { wants('review-api') } }
          steps {
            dir('review-api') { sh 'PATH="$WORKSPACE/.ci-venv/bin:$PATH" PYTEST_ADDOPTS="--junitxml=$WORKSPACE/reports/review-api-junit.xml" make test' }
            sh 'export PATH="$PWD/.ci-venv/bin:$PATH" && ci/schemathesis.sh 50'   // review-api vs contracts/review-api.openapi.yaml
            // Against the pod's postgres sidecar (PGHOST/PGPORT/PGUSER set on the tools container, values.yaml).
            sh 'PATH="$PWD/.ci-venv/bin:$PATH" ci/db-parity.sh'
          }
        }
        stage('scanner-worker') {
          when { expression { wants('scanner-worker') } }
          steps { dir('scanner-worker') { sh 'AISCAN_WORKER_PYTHON=$WORKSPACE/.ci-venv-worker/bin/python PATH="$WORKSPACE/.ci-venv-worker/bin:$PATH" PYTEST_ADDOPTS="--junitxml=$WORKSPACE/reports/scanner-worker-junit.xml" make test' } }
        }
      }
    }

    stage('Build') {
      when { expression { !skipBuild && changed } }
      stages {
        stage('frontend')       { when { expression { wants('frontend') } }       steps { script { kanikoBuild('frontend') } } }
        stage('gateway')        { when { expression { wants('gateway') } }        steps { script { kanikoBuild('gateway') } } }
        stage('review-api')     { when { expression { wants('review-api') } }     steps { script { kanikoBuild('review-api') } } }
        stage('scanner-worker') { when { expression { wants('scanner-worker') } } steps { script { kanikoBuild('scanner-worker') } } }
      }
    }

    stage('Scan') {
      when { expression { !skipBuild && changed } }
      stages {
        stage('frontend')       { when { expression { wants('frontend') } }       steps { script { trivyScan('frontend') } } }
        stage('gateway')        { when { expression { wants('gateway') } }        steps { script { trivyScan('gateway') } } }
        stage('review-api')     { when { expression { wants('review-api') } }     steps { script { trivyScan('review-api') } } }
        stage('scanner-worker') { when { expression { wants('scanner-worker') } } steps { script { trivyScan('scanner-worker') } } }
      }
    }

    stage('Push') {
      when { allOf { branch 'main'; expression { !skipBuild && changed } } }
      stages {
        stage('frontend')       { when { expression { wants('frontend') } }       steps { script { cranePush('frontend') } } }
        stage('gateway')        { when { expression { wants('gateway') } }        steps { script { cranePush('gateway') } } }
        stage('review-api')     { when { expression { wants('review-api') } }     steps { script { cranePush('review-api') } } }
        stage('scanner-worker') { when { expression { wants('scanner-worker') } } steps { script { cranePush('scanner-worker') } } }
      }
    }

    stage('Deploy') {
      when { allOf { branch 'main'; expression { !skipBuild && changed } } }
      steps { script {
        changed.each { s ->
          dir("deploy/${SERVICES[s][1]}/overlays/${params.TARGET}") {
            if (s != 'scanner-worker') {
              // frontend, gateway, review-api: images[] name ai-scan/<svc> -> newName <host>/ai-scan/<svc>, newTag <sha>
              sh "kustomize edit set image ai-scan/${s}=${img(s)}"
            } else {
              // scanner-worker: review-api reads SCANNER_IMAGE (configMapKeyRef) from the 'scanner-image' configMapGenerator.
              sh "kustomize edit set configmap scanner-image --from-literal=SCANNER_IMAGE=${img(s)}"
            }
          }
        }
        sh """
          git config user.name '${BOT_NAME}' && git config user.email '${BOT_EMAIL}'
          git add deploy/*/overlays/${params.TARGET}/kustomization.yaml
          git diff --cached --quiet || git commit -m "ci(${params.TARGET}): ${changed.join(',')} -> ${env.GIT_SHA} [skip ci]"
        """
        // ---------------------------------------------------------------------------
        // TODO(push): stub until Almog approves and creates the 'github-bot' credential.
        // withCredentials([usernamePassword(credentialsId: 'github-bot', usernameVariable: 'GU', passwordVariable: 'GT')]) {
        //   sh 'git push "https://${GU}:${GT}@github.com/almog975/ai-scan-repos.git" HEAD:main'
        // }
        // ---------------------------------------------------------------------------
      } }
    }
  }
  post {
    always {
      // Contracts always writes reports/contracts-junit.xml, so an empty match means a stage lost its report.
      script { if (!skipBuild) { junit allowEmptyResults: false, testResults: 'reports/*-junit.xml' } }
    }
  }
}
