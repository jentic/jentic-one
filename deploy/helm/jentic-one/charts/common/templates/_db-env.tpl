{{- /*
common.db-env: render JENTIC__DATABASES__* env vars for service pods.

DEVELOPMENT-ONLY pattern: passwords are injected as plain env-var values
read directly from values.yaml. This is fine for local kind clusters and
short-lived dev/test envs, but MUST NOT carry forward to production.

Production guidance:
  - Store DB credentials in a Kubernetes Secret (or external secret store
    like AWS Secrets Manager / Vault, fronted by external-secrets).
  - Replace the `value:` lines for *_PASSWORD (and likely *_USER) with
    `valueFrom: { secretKeyRef: { name: ..., key: ... } }`.
  - Keep host/port/name/schema as plain values; only credentials need
    secret handling.

See deploy/README.md "Production secrets" for the migration recipe.
*/ -}}
{{- define "common.db-env" -}}
{{- $pgHost := printf "%s-postgresql" .Release.Name -}}
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
  value: {{ include "common.require-install" (dict "root" $ "value" $db.password "message" (printf "global.databases.%s.password is required — set it in your values file (dev files: deploy/helm/values/local-*.yaml)" $surface)) | quote }}
- name: JENTIC__DATABASES__{{ upper $surface }}__SCHEMA_NAME
  value: {{ $db.schema_name | default $db.schema | quote }}
{{- end }}
{{- end -}}
