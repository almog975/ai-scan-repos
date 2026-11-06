{{- define "ai-scan.labels" -}}
app.kubernetes.io/part-of: ai-scan-repos
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/* image: include "ai-scan.image" (list $ "reviewApi") */}}
{{- define "ai-scan.image" -}}
{{- $root := index . 0 -}}
{{- $name := index $root.Values.images (index . 1) -}}
{{- with $root.Values.global.imageRegistry -}}{{ trimSuffix "/" . }}/{{- end -}}{{ $name }}:{{ $root.Values.global.imageTag }}
{{- end }}

{{- define "ai-scan.publicUrl" -}}
{{- if .Values.gateway.publicUrl -}}{{ .Values.gateway.publicUrl }}
{{- else if .Values.ingress.enabled -}}http://{{ .Values.ingress.host }}
{{- else -}}http://localhost:{{ .Values.frontend.port }}
{{- end -}}
{{- end }}

{{- define "ai-scan.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: {{ . }}
runAsGroup: {{ . }}
fsGroup: {{ . }}
fsGroupChangePolicy: OnRootMismatch
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "ai-scan.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end }}

{{- define "ai-scan.waitPostgres" -}}
- name: wait-postgres
  image: {{ include "ai-scan.image" (list . "reviewApi") }}
  imagePullPolicy: {{ .Values.global.imagePullPolicy }}
  command: [python, -c]
  args:
    - |
      import os, socket, time
      from urllib.parse import urlparse
      raw = os.environ["DATABASE_URL"].replace("postgresql+psycopg", "postgresql", 1)
      u = urlparse(raw)
      host, port = u.hostname, u.port or 5432
      for _ in range(90):
          try:
              s = socket.create_connection((host, port), 2)
              s.close()
              break
          except OSError:
              time.sleep(2)
      else:
          raise SystemExit(f"postgres not reachable at {host}:{port}")
  envFrom:
    - secretRef: {name: db-secrets}
  resources:
    requests: {cpu: 25m, memory: 64Mi}
    limits: {cpu: 200m, memory: 128Mi}
  securityContext:
    {{- include "ai-scan.containerSecurityContext" . | nindent 4 }}
  volumeMounts:
    - {name: tmp, mountPath: /tmp}
{{- end }}

{{- define "ai-scan.migrateContainer" -}}
- name: migrate
  image: {{ include "ai-scan.image" (list . "reviewApi") }}
  imagePullPolicy: {{ .Values.global.imagePullPolicy }}
  args: [alembic, upgrade, head]
  envFrom:
    - secretRef: {name: db-secrets}
  resources:
    requests: {cpu: 50m, memory: 128Mi}
    limits: {cpu: 500m, memory: 256Mi}
  securityContext:
    {{- include "ai-scan.containerSecurityContext" . | nindent 4 }}
  volumeMounts:
    - {name: tmp, mountPath: /tmp}
{{- end }}

{{/*
Auto-heal / monitoring values merged over built-in defaults (keep in sync with values.yaml;
ci/tests/test_helm_chart_sync.py checks it). `helm upgrade --reuse-values` from a release made
before these keys existed reuses the OLD chart's values, so .Values.autoheal / .Values.monitoring
can be nil; without this the templates fail with "nil pointer evaluating interface {}.enabled".
Usage: {{- $ah := include "ai-scan.autoheal" . | fromYaml }}
*/}}
{{- /* Fill keys missing from $v with $d, recursing into maps. Unlike sprig merge, an explicit false/0/"" is kept. */ -}}
{{- define "ai-scan.withDefaults" -}}
{{- $v := .v | default dict -}}
{{- $out := dict -}}
{{- range $k, $dv := .d -}}
{{- if and (kindIs "map" $dv) (hasKey $v $k) (kindIs "map" (index $v $k)) -}}
{{- $_ := set $out $k (include "ai-scan.withDefaults" (dict "v" (index $v $k) "d" $dv) | fromYaml) -}}
{{- else if hasKey $v $k -}}
{{- $_ := set $out $k (index $v $k) -}}
{{- else -}}
{{- $_ := set $out $k $dv -}}
{{- end -}}
{{- end -}}
{{- range $k, $vv := $v -}}
{{- if not (hasKey $out $k) -}}{{- $_ := set $out $k $vv -}}{{- end -}}
{{- end -}}
{{- toYaml $out -}}
{{- end -}}

{{- define "ai-scan.autoheal" -}}
{{- $d := dict "enabled" true
      "jenkins" (dict "enabled" true "namespace" "jenkins" "serviceAccount" "ai-scan-autoheal")
      "cronjob" (dict "enabled" false "schedule" "*/10 * * * *")
      "image" "alpine/k8s:1.31.4"
      "dryRun" false -}}
{{- include "ai-scan.withDefaults" (dict "v" .Values.autoheal "d" $d) -}}
{{- end -}}

{{- define "ai-scan.monitoring" -}}
{{- $d := dict "enabled" false "releaseLabel" "kube-prometheus-stack" "scrapeInterval" "30s" -}}
{{- include "ai-scan.withDefaults" (dict "v" .Values.monitoring "d" $d) -}}
{{- end -}}
