{{/*
common.service-account-name — pod-spec fragment selecting the service
account the app/broker pods run under. Emits nothing when unset (pods use
the namespace default), so non-Marketplace deployments are unchanged.

The AWS Marketplace launch experience substitutes the buyer-chosen account
into global.serviceAccount.name (delivery-option override parameter with
DefaultValue ${AWSMP_SERVICE_ACCOUNT}); on EKS that account carries the IAM
role (IRSA) the entitlement gate uses to call AWS.
*/}}
{{- define "common.service-account-name" -}}
{{- with (((.Values.global).serviceAccount).name) -}}
serviceAccountName: {{ . }}
{{- end -}}
{{- end -}}
