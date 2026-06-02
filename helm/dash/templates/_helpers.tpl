{{/*
Expand the name of the chart.
*/}}
{{- define "dash.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Create a fully qualified app name.
*/}}
{{- define "dash.fullname" -}}
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

{{/*
Chart name and version label value.
*/}}
{{- define "dash.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels
*/}}
{{- define "dash.labels" -}}
helm.sh/chart: {{ include "dash.chart" . }}
{{ include "dash.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app: dash
{{- end -}}

{{/*
Selector labels
*/}}
{{- define "dash.selectorLabels" -}}
app.kubernetes.io/name: {{ include "dash.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
ServiceAccount name
*/}}
{{- define "dash.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "dash.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Image pull secrets list — merges global + per-image lists.
*/}}
{{- define "dash.imagePullSecrets" -}}
{{- $secrets := concat (default (list) .Values.imagePullSecrets) (default (list) .Values.image.pullSecrets) -}}
{{- if $secrets -}}
imagePullSecrets:
{{- range $secrets }}
  - name: {{ .name | default . }}
{{- end -}}
{{- end -}}
{{- end -}}
