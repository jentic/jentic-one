{{- /*
common.app-secrets.{env,volume,mount} — mount the release's generated
application secrets (see the umbrella chart's templates/app-secrets.yaml:
credential-encryption keyset, admin JWT secret, invite pepper, connect
state secret) into a service pod as the JENTIC_CONFIG_FILE.

Active when global.appSecrets.generate=true or .existingSecret is set; emits
nothing otherwise, so every deployment template can include it
unconditionally. The app, broker, and control pods must all mount the SAME
Secret — they cross-verify JWTs and cross-decrypt credentials — which is
why this is a global, release-scoped mount.

Mutually exclusive with configFile.contents: both claim JENTIC_CONFIG_FILE
(the config loader reads a single file), so setting both is a config error
we fail loudly on. Dev overlays use configFile (secrets inline, dev-only);
Marketplace/production use this Secret.
*/ -}}

{{- define "common.app-secrets.enabled" -}}
{{- $sec := (.Values.global).appSecrets | default dict -}}
{{- if or $sec.generate $sec.existingSecret -}}
{{- if and .Values.configFile .Values.configFile.contents -}}
{{- fail "global.appSecrets and configFile.contents are mutually exclusive (both claim JENTIC_CONFIG_FILE) — put the secrets in one place" -}}
{{- end -}}
true
{{- end -}}
{{- end -}}

{{- define "common.app-secrets.secret-name" -}}
{{- $sec := (.Values.global).appSecrets | default dict -}}
{{- $sec.existingSecret | default (printf "%s-app-secrets" .Release.Name) -}}
{{- end -}}

{{- define "common.app-secrets.env" -}}
{{- if (include "common.app-secrets.enabled" .) }}
- name: JENTIC_CONFIG_FILE
  value: /etc/jentic/app-secrets/config.yaml
{{- end }}
{{- end -}}

{{- define "common.app-secrets.volume" -}}
{{- if (include "common.app-secrets.enabled" .) }}
- name: jentic-app-secrets
  secret:
    secretName: {{ include "common.app-secrets.secret-name" . }}
{{- end }}
{{- end -}}

{{- define "common.app-secrets.mount" -}}
{{- if (include "common.app-secrets.enabled" .) }}
- name: jentic-app-secrets
  mountPath: /etc/jentic/app-secrets
  readOnly: true
{{- end }}
{{- end -}}
