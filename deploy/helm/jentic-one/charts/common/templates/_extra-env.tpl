{{- /*
common.extra-env: render the operator's `extraEnv` map into a pod's env list.

Include this AFTER every one of the chart's own env helpers. Kubernetes resolves
a duplicate env name to the LAST entry in the container's list, so include order
is exactly what decides whether `--set <svc>.extraEnv.FOO=bar` takes effect or
is silently overridden by a chart-supplied FOO. Last means the operator's value
wins, which is the only defensible precedence for an escape hatch: a chart
default that beats an explicit override leaves no way to set that key at all.

Helpers that own a key the operator may also want to set should skip it when
`extraEnv` already carries it (see common.runtime-env), so the rendered list
holds one entry rather than a duplicate pair that only reads correctly if you
know the last-wins rule.
*/ -}}
{{- define "common.extra-env" -}}
{{- range $k, $v := .Values.extraEnv }}
- name: {{ $k }}
  value: {{ $v | quote }}
{{- end }}
{{- end -}}
