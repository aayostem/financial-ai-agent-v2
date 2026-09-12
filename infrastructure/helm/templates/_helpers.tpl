{{/*
Expand the name of the chart.
*/}}
{{- define "financial-ai-agent.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "financial-ai-agent.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "financial-ai-agent.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "financial-ai-agent.labels" -}}
helm.sh/chart: {{ include "financial-ai-agent.chart" . }}
{{ include "financial-ai-agent.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "financial-ai-agent.selectorLabels" -}}
app.kubernetes.io/name: {{ include "financial-ai-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "financial-ai-agent.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "financial-ai-agent.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Resolved Postgres host
*/}}
{{- define "financial-ai-agent.postgresHost" -}}
{{- if .Values.postgres.host }}
{{- .Values.postgres.host }}
{{- else }}
{{- printf "%s-pgvector" (include "financial-ai-agent.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Resolved Redis host
*/}}
{{- define "financial-ai-agent.redisHost" -}}
{{- if .Values.redisConfig.host }}
{{- .Values.redisConfig.host }}
{{- else }}
{{- printf "%s-redis" (include "financial-ai-agent.fullname" .) }}
{{- end }}
{{- end }}