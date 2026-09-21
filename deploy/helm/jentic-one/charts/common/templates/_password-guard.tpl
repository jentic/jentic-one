{{- /*
common.assert-string-password: fail the install when a password value reached
the chart as a non-string.

YAML types unquoted scalars before Helm ever sees them, so a digit-only
password in a values file arrives as a number and renders through its Go
formatting: `password: 0123456789` becomes `1.23456789e+08`, and the pod then
authenticates with a string the operator never typed. Truncated leading zeros
and `yes`/`no`/`on`/`off` booleans fail the same way.

There is no render-time repair — the original text is gone by the time a
template runs — so the only honest handling is to refuse the install and name
the fix (quote it).

Args: dict with "value" (the password as received) and "path" (the values key,
for the message). Call it only inside the branch that already established the
value is non-empty: an unset password is nil, which is not a string either, and
the missing-password case has its own guard (common.require-install).
*/ -}}
{{- define "common.assert-string-password" -}}
{{- if not (kindIs "string" .value) -}}
{{- fail (printf "%s must be quoted in your values file — YAML parsed it as a %s, so it renders as %q rather than the value you typed. Write it as a quoted string: %s: \"...\"" .path (kindOf .value) (printf "%v" .value) .path) -}}
{{- end -}}
{{- end -}}
