{{- /*
common.encryption.{env,volume,mount} — mount the release's credential
encryption keyset (see the umbrella chart's templates/encryption-secret.yaml)
into a service pod as the JENTIC_CONFIG_FILE.

Active when global.encryption.generate=true or .existingSecret is set; emits
nothing otherwise, so every deployment template can include it
unconditionally. Both the app/control surfaces (encrypt on credential write)
and the broker (decrypt + re-encrypt on token refresh) need the SAME keyset,
which is why this is a global, release-scoped mount.

Mutually exclusive with configFile.contents: both claim JENTIC_CONFIG_FILE
(the config loader reads a single file), so setting both is a config error
we fail loudly on. Dev overlays use configFile (keyset inline, dev-only);
Marketplace/production use this Secret.
*/ -}}

{{- define "common.encryption.enabled" -}}
{{- $enc := (.Values.global).encryption | default dict -}}
{{- if or $enc.generate $enc.existingSecret -}}
{{- if and .Values.configFile .Values.configFile.contents -}}
{{- fail "global.encryption and configFile.contents are mutually exclusive (both claim JENTIC_CONFIG_FILE) — put the keyset in one place" -}}
{{- end -}}
true
{{- end -}}
{{- end -}}

{{- define "common.encryption.secret-name" -}}
{{- $enc := (.Values.global).encryption | default dict -}}
{{- $enc.existingSecret | default (printf "%s-encryption" .Release.Name) -}}
{{- end -}}

{{- define "common.encryption.env" -}}
{{- if (include "common.encryption.enabled" .) }}
- name: JENTIC_CONFIG_FILE
  value: /etc/jentic/encryption/config.yaml
{{- end }}
{{- end -}}

{{- define "common.encryption.volume" -}}
{{- if (include "common.encryption.enabled" .) }}
- name: jentic-encryption
  secret:
    secretName: {{ include "common.encryption.secret-name" . }}
{{- end }}
{{- end -}}

{{- define "common.encryption.mount" -}}
{{- if (include "common.encryption.enabled" .) }}
- name: jentic-encryption
  mountPath: /etc/jentic/encryption
  readOnly: true
{{- end }}
{{- end -}}
