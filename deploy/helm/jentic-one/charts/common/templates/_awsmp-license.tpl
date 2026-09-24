{{/*
common.awsmp-license.{volume,mount} — AWS Marketplace license secret.

The Marketplace launch experience substitutes the name of a Kubernetes
Secret it creates into global.awsmp.licenseSecret (delivery-option override
parameter with DefaultValue ${AWSMP_LICENSE_SECRET}). When set, the secret
is mounted read-only into the app/broker pods; when unset (every
non-Marketplace deployment) both helpers emit nothing.

The mount is reserved for AWS's license-file flows (e.g. EKS Anywhere) —
the IRSA-based entitlement gate calls the AWS APIs directly and does not
read it.
*/}}
{{- define "common.awsmp-license.volume" -}}
{{- with (((.Values.global).awsmp).licenseSecret) -}}
- name: awsmp-license
  secret:
    secretName: {{ . }}
{{- end -}}
{{- end -}}

{{- define "common.awsmp-license.mount" -}}
{{- if (((.Values.global).awsmp).licenseSecret) -}}
- name: awsmp-license
  mountPath: /var/run/secrets/aws-marketplace/license
  readOnly: true
{{- end -}}
{{- end -}}
