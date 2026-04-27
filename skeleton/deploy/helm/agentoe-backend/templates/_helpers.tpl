{{/*
공통 helper 들. 다른 차트 (vbgw, frontend) 도 동일 구조로 카피해 사용.
*/}}

{{- define "agentoe-backend.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agentoe-backend.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "agentoe-backend.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "agentoe-backend.labels" -}}
helm.sh/chart: {{ include "agentoe-backend.chart" . }}
{{ include "agentoe-backend.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: agentoe
{{- end -}}

{{- define "agentoe-backend.selectorLabels" -}}
app.kubernetes.io/name: {{ include "agentoe-backend.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "agentoe-backend.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "agentoe-backend.fullname" .) .Values.serviceAccount.name }}
{{- else -}}
{{- default "default" .Values.serviceAccount.name }}
{{- end -}}
{{- end -}}

{{/*
checksum 으로 ConfigMap/Secret 변경 시 Pod rolling restart 강제.
ExternalSecret 결과물은 직접 hash 못 잡으므로 secrets refresh 는 ESO 가 처리.
*/}}
{{- define "agentoe-backend.configChecksum" -}}
{{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
{{- end -}}
