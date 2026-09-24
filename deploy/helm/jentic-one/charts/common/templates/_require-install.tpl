{{- /*
common.require-install — `required` that only fires against a live cluster.

AWS Marketplace validates Helm chart products by running bare `helm lint`
and `helm template` (no overrides, no cluster) and rejects charts that fail
either (INVALID_HELM_LINT / INVALID_HELM_TEMPLATE). A hard `required` on
passwords therefore can't be unconditional. `lookup` returns empty during
offline rendering but queries the API server during a real install/upgrade,
so this helper:

  - returns the value when set;
  - fails loudly (the original `required` behavior) when unset AND a live
    cluster is detectable — i.e. every `helm install`/`upgrade`;
  - renders an unmistakable placeholder when unset in offline renders
    (`helm template`/`lint`), keeping validation tooling green.

The cluster probe checks the release namespace's default ServiceAccount and
falls back to listing namespaces, so namespace-scoped installers still trip
the guard. Anyone piping `helm template` straight into kubectl bypasses
Helm's install path and gets the placeholder — visibly broken on purpose.

Usage:
  {{ include "common.require-install" (dict "root" $ "value" $db.password "message" "...") }}
*/ -}}
{{- define "common.require-install" -}}
{{- if .value -}}
{{- .value -}}
{{- else -}}
{{- $root := .root -}}
{{- $live := or (lookup "v1" "ServiceAccount" $root.Release.Namespace "default") (lookup "v1" "Namespace" "" "") -}}
{{- if $live -}}
{{- required .message nil -}}
{{- else -}}
REQUIRED-AT-INSTALL
{{- end -}}
{{- end -}}
{{- end -}}
