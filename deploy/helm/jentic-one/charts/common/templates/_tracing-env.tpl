{{- /*
common.tracing-env: the app-side trace sampling ratio.

`global.observability.otel.samplingRatio` maps to
`JENTIC__OBSERVABILITY__TRACING__SAMPLE_RATIO` (config field
`observability.tracing.sample_ratio`, see docs/reference/config.md): the
fraction of traces the application records and exports, including requests
that arrive with a sampled `traceparent`.

Rendered only when the value is set. Env vars beat a mounted config file in the
settings precedence, so emitting a default here would quietly override
`sample_ratio` from a `configFile`/appSecrets config.yaml. `toString` keeps a
`--set ...samplingRatio=0` (parsed as the integer 0, which is falsy) rendering.
*/ -}}
{{- define "common.tracing-env" -}}
{{- $ratio := .Values.global.observability.otel.samplingRatio }}
{{- if and (not (kindIs "invalid" $ratio)) (ne (toString $ratio) "") }}
- name: JENTIC__OBSERVABILITY__TRACING__SAMPLE_RATIO
  value: {{ toString $ratio | quote }}
{{- end }}
{{- end -}}
