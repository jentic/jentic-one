{{- /*
common.app-secrets.{env,volume,mount} — hand a service pod the release's
application secrets (see the umbrella chart's templates/app-secrets.yaml:
credential-encryption keyset, admin JWT secret, invite pepper, connect state
secret).

Active when global.appSecrets.generate=true or .existingSecret is set; emits
nothing otherwise, so every deployment template can include it
unconditionally.

Two layouts (global.appSecrets.layout, see common.app-secrets.layout):

  split  -> each surface gets ONLY the secrets its code reads
            (common.app-secrets.concerns), one Secret key per concern:
              credentials-encryption.yaml  file, mounted as JENTIC_CONFIG_FILE
                                           (list-shaped, so env can't carry it)
              admin-jwt-secret      env JENTIC__ADMIN__AUTH__JWT_SECRET
              admin-invite-pepper   env JENTIC__ADMIN__INVITE__PEPPER
              connect-state-secret  env JENTIC__CREDENTIALS__CONNECT__STATE_SECRET
            The scalar keys ride secretKeyRef env (like the db-password-*
            keys), so a surface without the keyset mounts no file at all.
  single -> the legacy layout: one `config.yaml` key holding everything,
            mounted as JENTIC_CONFIG_FILE on every surface.

Cross-surface agreement still holds in split mode: every surface that reads a
secret references the same Secret key (the broker re-encrypts refreshed
tokens control must read back; control and registry verify the JWTs admin
issues).

The JENTIC_CONFIG_FILE claim is mutually exclusive with configFile.contents
(the config loader reads a single file), so setting both on a pod that mounts
the app-secrets file is a config error we fail loudly on. In split mode a
surface that mounts no file (registry) may still use configFile.
*/ -}}

{{- define "common.app-secrets.active" -}}
{{- $sec := (.Values.global).appSecrets | default dict -}}
{{- if or $sec.generate $sec.existingSecret -}}true{{- end -}}
{{- end -}}

{{- /*
common.app-secrets.layout: "split" or "single". Empty (the default) picks
split for chart-generated Secrets and single for existingSecret, so an
existing Secret holding only the legacy config.yaml key keeps working
unchanged until its owner adds the per-concern keys and opts in.
*/ -}}
{{- define "common.app-secrets.layout" -}}
{{- $sec := (.Values.global).appSecrets | default dict -}}
{{- $layout := $sec.layout | default "" -}}
{{- if not (has $layout (list "" "split" "single")) -}}
{{- fail (printf "global.appSecrets.layout must be \"split\", \"single\", or empty (auto) — got %q" $layout) -}}
{{- end -}}
{{- if $layout -}}
{{- $layout -}}
{{- else if $sec.existingSecret -}}
single
{{- else -}}
split
{{- end -}}
{{- end -}}

{{- /*
common.app-secrets.concerns: space-separated secrets this surface reads, keyed
by the subchart name. Verified against src/jentic_one (and mirrored by
shared/config.py GUARDED_FIELD_SURFACES, which relaxes the production guard for
secrets a standalone surface never reads):

  app      (registry+admin+control+auth)  everything
  admin    (admin+auth)   keyset (provider-config client secrets), JWT
                          secret (issues + verifies), invite pepper
  control                 keyset (credential writes), JWT secret (verifies),
                          connect state secret (signs OAuth state)
  registry                JWT secret (verifies)
  broker                  keyset (decrypts / re-encrypts credentials); it
                          verifies with broker.jwt_secret / trusted issuers,
                          not the admin JWT secret
*/ -}}
{{- define "common.app-secrets.concerns" -}}
{{- $map := dict
  "app" "encryption jwt pepper state"
  "admin" "encryption jwt pepper"
  "control" "encryption jwt state"
  "registry" "jwt"
  "broker" "encryption" -}}
{{- index $map .Chart.Name | default "encryption jwt pepper state" -}}
{{- end -}}

{{- /* True when this pod mounts an app-secrets file as JENTIC_CONFIG_FILE. */ -}}
{{- define "common.app-secrets.file-claimed" -}}
{{- if (include "common.app-secrets.active" .) -}}
{{- $claimed := or (eq (include "common.app-secrets.layout" .) "single") (has "encryption" (splitList " " (include "common.app-secrets.concerns" .))) -}}
{{- if $claimed -}}
{{- if and .Values.configFile .Values.configFile.contents -}}
{{- fail "global.appSecrets and configFile.contents are mutually exclusive (both claim JENTIC_CONFIG_FILE) — put the secrets in one place" -}}
{{- end -}}
true
{{- end -}}
{{- end -}}
{{- end -}}

{{- /* Kept for callers that only need "is app-secrets on" (common.db-env). */ -}}
{{- define "common.app-secrets.enabled" -}}
{{- include "common.app-secrets.active" . -}}
{{- end -}}

{{- define "common.app-secrets.secret-name" -}}
{{- $sec := (.Values.global).appSecrets | default dict -}}
{{- $sec.existingSecret | default (printf "%s-app-secrets" .Release.Name) -}}
{{- end -}}

{{- /* The Secret key (and file name) this pod's JENTIC_CONFIG_FILE comes from. */ -}}
{{- define "common.app-secrets.file-key" -}}
{{- if eq (include "common.app-secrets.layout" .) "single" -}}
config.yaml
{{- else -}}
credentials-encryption.yaml
{{- end -}}
{{- end -}}

{{- define "common.app-secrets.env" -}}
{{- if (include "common.app-secrets.active" .) }}
{{- if (include "common.app-secrets.file-claimed" .) }}
- name: JENTIC_CONFIG_FILE
  value: /etc/jentic/app-secrets/{{ include "common.app-secrets.file-key" . }}
{{- end }}
{{- if eq (include "common.app-secrets.layout" .) "split" }}
{{- $concerns := splitList " " (include "common.app-secrets.concerns" .) }}
{{- $extra := .Values.extraEnv | default dict }}
{{- $secretName := include "common.app-secrets.secret-name" . }}
{{- $scalars := list
  (list "jwt" "JENTIC__ADMIN__AUTH__JWT_SECRET" "admin-jwt-secret")
  (list "pepper" "JENTIC__ADMIN__INVITE__PEPPER" "admin-invite-pepper")
  (list "state" "JENTIC__CREDENTIALS__CONNECT__STATE_SECRET" "connect-state-secret") }}
{{- range $s := $scalars }}
{{- if and (has (index $s 0) $concerns) (not (hasKey $extra (index $s 1))) }}
- name: {{ index $s 1 }}
  valueFrom:
    secretKeyRef:
      name: {{ $secretName }}
      key: {{ index $s 2 }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "common.app-secrets.volume" -}}
{{- if (include "common.app-secrets.file-claimed" .) }}
{{- $key := include "common.app-secrets.file-key" . }}
- name: jentic-app-secrets
  secret:
    secretName: {{ include "common.app-secrets.secret-name" . }}
    # Only this surface's config file lands on disk; the other keys in the
    # same Secret reach pods as secretKeyRef env (or not at all).
    items:
      - key: {{ $key }}
        path: {{ $key }}
{{- end }}
{{- end -}}

{{- define "common.app-secrets.mount" -}}
{{- if (include "common.app-secrets.file-claimed" .) }}
- name: jentic-app-secrets
  mountPath: /etc/jentic/app-secrets
  readOnly: true
{{- end }}
{{- end -}}
