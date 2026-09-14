{{- /*
common.db-env: render JENTIC__DATABASES__* env vars for service pods.

Password source, in order of precedence:
  1. global.databases.<surface>.password set  -> plain env value. Dev-only
     for real data (values files are not secret storage) — external-DB
     production deployments should prefer a Secret they manage; this arm is
     also what lets an external-DB install override the generated defaults.
  2. global.appSecrets active                 -> secretKeyRef into the
     release's generated app-secrets Secret (db-password-<surface> key,
     created by templates/app-secrets.yaml). The bundled Postgres init
     script creates the roles from the same keys, so the pair always agrees.
  3. neither                                  -> install-time failure
     (common.require-install; offline lint/template render an inert
     REQUIRED-AT-INSTALL placeholder).

Host/port/name/schema are not secrets and stay plain values.
See docs/installation/helm.md "Secrets".
*/ -}}
{{- define "common.db-env" -}}
{{- $pgHost := printf "%s-postgresql" .Release.Name -}}
{{- $ctx := . -}}
{{- range $surface, $db := .Values.global.databases }}
- name: JENTIC__DATABASES__{{ upper $surface }}__HOST
  value: {{ (ternary $pgHost $db.host $.Values.global.postgresql.enabled) | quote }}
- name: JENTIC__DATABASES__{{ upper $surface }}__PORT
  value: {{ $db.port | default 5432 | quote }}
- name: JENTIC__DATABASES__{{ upper $surface }}__NAME
  value: {{ $db.name | quote }}
- name: JENTIC__DATABASES__{{ upper $surface }}__USER
  value: {{ $db.user | quote }}
- name: JENTIC__DATABASES__{{ upper $surface }}__PASSWORD
{{- if $db.password }}
  value: {{ $db.password | quote }}
{{- else if (include "common.app-secrets.enabled" $ctx) }}
  valueFrom:
    secretKeyRef:
      name: {{ include "common.app-secrets.secret-name" $ctx }}
      key: db-password-{{ $surface }}
{{- else }}
  value: {{ include "common.require-install" (dict "root" $ "value" $db.password "message" (printf "global.databases.%s.password is required — set it in your values file (dev files: deploy/helm/values/local-*.yaml)" $surface)) | quote }}
{{- end }}
- name: JENTIC__DATABASES__{{ upper $surface }}__SCHEMA_NAME
  value: {{ $db.schema_name | default $db.schema | quote }}
{{- end }}
{{- end -}}
