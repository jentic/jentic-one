{{- /*
common.image: render the full image reference for a service pod.

Resolution order for the tag:
  1. .Values.image.tag                 (per-service override in subchart values)
  2. .Values.global.image.tag          (umbrella-wide pin, set by Makefile from pyproject)
  3. .Chart.AppVersion                 (the subchart's own appVersion — note that
                                        .Chart is the SUBCHART here, so each one
                                        carries its own release-please-bumped
                                        appVersion; the umbrella's is not visible)

There is deliberately no `:latest` arm. A floating tag makes the running version
unknowable and a rollout unrepeatable, and every install already reaches step 3,
so falling through means a subchart lost its appVersion — an authoring mistake
worth failing on rather than papering over.

Resolution order for the repository:
  1. .Values.image.repository          (per-service value in subchart values)
  2. printf "%s/%s" .Values.global.image.registry .Chart.Name (when registry is set)
*/ -}}
{{- define "common.image" -}}
{{- $tag := .Values.image.tag | default (default "" .Values.global.image.tag) | default .Chart.AppVersion -}}
{{- if not $tag -}}
  {{- fail (printf "%s: no image tag resolved — set image.tag, global.image.tag, or restore appVersion in charts/%s/Chart.yaml" .Chart.Name .Chart.Name) -}}
{{- end -}}
{{- $repo := .Values.image.repository -}}
{{- if and (not $repo) .Values.global.image.registry -}}
  {{- $repo = printf "%s/%s" .Values.global.image.registry .Chart.Name -}}
{{- end -}}
{{- if not $repo -}}
  {{- fail "image.repository or global.image.registry must be set" -}}
{{- end -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
