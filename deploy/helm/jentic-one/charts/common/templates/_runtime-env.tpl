{{- /*
common.runtime-env: JENTIC_ENV for every Python surface.

JENTIC_ENV is what arms the config loader's placeholder-secret guard
(shared/config.py _require_or_generate_secret). In `development` — the
application's own default when the variable is absent — an empty or `change-me`
value for the admin JWT secret, the invite pepper, or the connect state secret is
replaced by a random per-process secret. That is right for dev and silently wrong
for a real install: each replica signs with its own key, so sessions and invites
break across pods, and a restart invalidates everything it issued.

So the chart states it rather than leaving it to the operator, and the default is
`production` — fail-closed. A production install missing its secrets stops at
boot with a config error naming the field instead of coming up and misbehaving
under load.

Dev overlays (deploy/helm/values/local-*.yaml) set `global.jenticEnv: development`
because they deliberately run without those secrets.

`extraEnv` wins: common.extra-env renders after this helper, and skipping the key
here when the operator set it keeps one entry in the rendered list rather than a
duplicate pair whose effective value depends on knowing Kubernetes' last-wins
rule.
*/ -}}
{{- define "common.runtime-env" -}}
{{- if not (hasKey (.Values.extraEnv | default dict) "JENTIC_ENV") }}
- name: JENTIC_ENV
  value: {{ .Values.global.jenticEnv | default "production" | quote }}
{{- end }}
{{- end -}}
