{{- /*
Pod- and container-level securityContext for the Python service pods.

The defaults in each subchart's values.yaml are the hardened set
docs/security/README.md asks for: non-root with a pinned uid, no privilege
escalation, every Linux capability dropped, the runtime's default seccomp
profile, and a read-only root filesystem.

Two couplings worth knowing before changing them:

  runAsUser 10001 pairs with `USER 10001` in deploy/docker/*.Dockerfile.
    runAsNonRoot alone is not enough — kubelet refuses to start a container when
    it cannot prove the image user is non-root, and it cannot resolve a username
    to a uid. Change one and you change both.

  readOnlyRootFilesystem pairs with the emptyDir the deployments mount at /tmp.
    The application writes nothing else outside its mounts, but Python and
    anything using tempfile need a writable /tmp.

Both are values, so an operator whose cluster policy disagrees can override them
per subchart without patching templates.
*/ -}}

{{- define "common.pod-security-context" -}}
{{- with .Values.podSecurityContext }}
securityContext:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "common.container-security-context" -}}
{{- with .Values.securityContext }}
securityContext:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- /*
The writable /tmp readOnlyRootFilesystem requires, rendered only when that
setting is actually on so an operator who turns it off does not carry a pointless
volume. Paired halves: common.tmp-volume (pod) and common.tmp-volume-mount
(container).
*/ -}}
{{- define "common.tmp-volume" -}}
{{- if (.Values.securityContext).readOnlyRootFilesystem }}
- name: tmp
  emptyDir: {}
{{- end }}
{{- end -}}

{{- define "common.tmp-volume-mount" -}}
{{- if (.Values.securityContext).readOnlyRootFilesystem }}
- name: tmp
  mountPath: /tmp
{{- end }}
{{- end -}}
