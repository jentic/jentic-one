{{- /*
common.logging-env: log verbosity plus the service's telemetry identity.

`global.observability.logging.level` maps to the app's real knob,
`JENTIC__RUNTIME__LOG_LEVEL` (config field `runtime.log_level`, see
docs/reference/config.md) — bare `LOG_LEVEL`/`LOG_FORMAT` names match nothing the
application reads, so they are not emitted.

Rendered only when the value is set. Env vars beat a mounted config file in the
settings precedence, so emitting a default here would quietly override
`runtime.log_level` from a `configFile`/appSecrets config.yaml and leave no way
to set it there. Empty (the default) means zero env entries and the application's
own default applies.

There is no `format` counterpart: the application has no log-format config
field, so the chart offers no knob for it.
*/ -}}
{{- define "common.logging-env" -}}
{{- with .Values.global.observability.logging.level }}
- name: JENTIC__RUNTIME__LOG_LEVEL
  value: {{ . | quote }}
{{- end }}
- name: OTEL_SERVICE_NAME
  value: {{ printf "%s-%s" .Release.Name .Chart.Name | quote }}
{{- end -}}
