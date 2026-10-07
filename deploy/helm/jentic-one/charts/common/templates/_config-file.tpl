{{- /*
common.config-file: mount an optional YAML config file into a service pod.

When a subchart sets `.Values.configFile.contents` (a YAML map), these helpers
render a ConfigMap holding that file, mount it read-only at a fixed path, and
export JENTIC_CONFIG_FILE so the app's config loader reads it (YAML file first,
then JENTIC__* env overrides on top — see shared/config.load_config).

This exists because some config is a *list* (e.g.
credentials.encryption.entries) which the flat JENTIC__SECTION__KEY env-var
convention cannot express. The smoke overlays use it to supply a dev-only
credential-encryption keyset so credential writes don't 500 in-cluster.

DEVELOPMENT-ONLY: contents land in a plain ConfigMap (not a Secret). Fine for
local kind/smoke clusters; never put real secrets here. See
docs/installation/helm.md "Secrets".
Production secrets use global.appSecrets instead (_app-secrets.tpl) — the two
are mutually exclusive since both claim JENTIC_CONFIG_FILE.

All three helpers no-op unless `.Values.configFile.contents` is set, so they are
safe to include unconditionally in every service deployment template.
*/ -}}

{{- /* The in-container path the rendered config file is mounted at. */ -}}
{{- define "common.config-file.path" -}}
/etc/jentic/config.yaml
{{- end -}}

{{- /* The env var pointing the loader at the mounted file. */ -}}
{{- define "common.config-file.env" -}}
{{- if .Values.configFile }}
{{- if .Values.configFile.contents }}
- name: JENTIC_CONFIG_FILE
  value: {{ include "common.config-file.path" . | quote }}
{{- end }}
{{- end }}
{{- end -}}

{{- /* The volume sourcing the ConfigMap. Emits nothing when unset. */ -}}
{{- define "common.config-file.volume" -}}
{{- if .Values.configFile }}
{{- if .Values.configFile.contents }}
- name: jentic-config
  configMap:
    name: {{ .Release.Name }}-{{ .Chart.Name }}-config
{{- end }}
{{- end }}
{{- end -}}

{{- /* The read-only mount of that volume into the container. */ -}}
{{- define "common.config-file.mount" -}}
{{- if .Values.configFile }}
{{- if .Values.configFile.contents }}
- name: jentic-config
  mountPath: {{ include "common.config-file.path" . }}
  subPath: config.yaml
  readOnly: true
{{- end }}
{{- end }}
{{- end -}}

{{- /* The ConfigMap object itself. Rendered as a full document. */ -}}
{{- define "common.config-file.configmap" -}}
{{- if .Values.configFile }}
{{- if .Values.configFile.contents }}
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ .Release.Name }}-{{ .Chart.Name }}-config
  labels:
    app.kubernetes.io/name: {{ .Chart.Name }}
    app.kubernetes.io/instance: {{ .Release.Name }}
data:
  config.yaml: |
{{ toYaml .Values.configFile.contents | indent 4 }}
{{- end }}
{{- end }}
{{- end -}}
