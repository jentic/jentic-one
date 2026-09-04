# Installing from AWS Marketplace

Jentic One is available as a [container product on AWS
Marketplace](https://aws.amazon.com/marketplace), deployed to Amazon EKS with
the Helm delivery option. The Marketplace build is functionally identical to
the open-source release, plus a runtime license check tied to your
subscription.

The chart is zero-touch: every secret (credential-encryption keyset, JWT
signing secret, database passwords) is generated on first install and reused
on every upgrade. A bundled PostgreSQL runs in-cluster by default; an
external database such as RDS is supported.

For self-hosted installs outside AWS Marketplace, use the
[installation index](README.md) instead.

## Prerequisites

You need an EKS cluster (Kubernetes 1.29+) and the `aws`, `kubectl`,
`helm` (≥ 3.8) and `eksctl` CLIs.

### Persistent storage

Fresh EKS clusters ship without the EBS CSI driver and without a default
StorageClass; the bundled PostgreSQL needs both. Skip this if your cluster
already has a default StorageClass. Symptom when it's missing: the
`postgresql` pod stays `Pending` with "pod has unbound immediate
PersistentVolumeClaims".

```bash
eksctl create iamserviceaccount \
  --name ebs-csi-controller-sa --namespace kube-system \
  --cluster <cluster> --region <region> \
  --role-name <cluster>-ebs-csi-role --role-only \
  --attach-policy-arn arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy \
  --approve

eksctl create addon --cluster <cluster> --region <region> \
  --name aws-ebs-csi-driver \
  --service-account-role-arn arn:aws:iam::<account-id>:role/<cluster>-ebs-csi-role

kubectl annotate storageclass gp2 \
  storageclass.kubernetes.io/is-default-class=true
```

### IAM-enabled service account (IRSA)

The pods verify your Marketplace entitlement through an IAM role attached to
a Kubernetes service account. The account name must match what your
Marketplace Launch page shows (default: `jentic-one`):

```bash
# one-time per cluster, unless created with --with-oidc
eksctl utils associate-iam-oidc-provider \
  --cluster <cluster> --region <region> --approve

eksctl create iamserviceaccount \
  --name jentic-one --namespace jentic-one \
  --cluster <cluster> --region <region> \
  --attach-policy-arn arn:aws:iam::aws:policy/AWSMarketplaceMeteringRegisterUsage \
  --attach-policy-arn arn:aws:iam::aws:policy/service-role/AWSLicenseManagerConsumptionPolicy \
  --approve
```

This creates the namespace, the IAM role, and the annotated service account.
The chart does not recreate it — it only references the name.

## Install

Use the commands generated on your Marketplace Launch page — they substitute
the service account and license-secret values for you. They follow this
shape:

```bash
aws ecr get-login-password --region us-east-1 \
  | helm registry login --username AWS --password-stdin \
    709825985650.dkr.ecr.us-east-1.amazonaws.com

helm install jentic-one \
  oci://709825985650.dkr.ecr.us-east-1.amazonaws.com/jentic/charts/jentic-one \
  --version <version> --namespace jentic-one --create-namespace \
  --set global.serviceAccount.name=jentic-one \
  --set global.awsmp.licenseSecret=<from-launch-page>
```

No passwords or further configuration are required at install time.

## Post-install: set the canonical base URL

Required before agents can connect — set it to the URL your agents will reach
the app at (your ingress or load-balancer URL). Why the value must match what
agents register with byte-for-byte (including the `localhost` vs `127.0.0.1`
trap): [Helm guide, step 3](helm.md#3-set-the-canonical-base-url).

```bash
helm upgrade jentic-one \
  oci://709825985650.dkr.ecr.us-east-1.amazonaws.com/jentic/charts/jentic-one \
  --version <version> -n jentic-one --reuse-values \
  --set app.extraEnv.JENTIC__AUTH__CANONICAL_BASE_URL=https://jentic.example.com
```

## Verify

```bash
kubectl -n jentic-one get pods
# expect: app, broker, postgresql — all Running

kubectl -n jentic-one port-forward svc/jentic-one-app 8000:8000
curl -s http://localhost:8000/health   # {"status":"ok","version":"<version>"}
```

Open `<your URL>/app` to create the first admin user, then connect an agent:

```bash
jentic register --url <app URL> --broker-url <broker URL>
```

Expose the broker Service (port 8000 in-cluster) alongside the app — it is
the data plane every agent call goes through. See
[the first brokered call](../guides/first-call.md) to take it from there.

## External database

To use RDS/Aurora PostgreSQL instead of the bundled database, disable the
bundled instance and point each surface at your endpoint. Explicit passwords
always take precedence over the generated ones:

```bash
--set postgresql.enabled=false \
--set global.postgresql.enabled=false \
--set global.databases.registry.host=<endpoint> \
--set global.databases.control.host=<endpoint> \
--set global.databases.admin.host=<endpoint> \
--set global.databases.registry.password=<...> \
--set global.databases.control.password=<...> \
--set global.databases.admin.password=<...>
```

Create the three roles and schemas (`registry`, `control`, `admin`) on the
instance first.

## Upgrades and removal

Upgrade to a new version with `--reset-values`, re-passing your explicit
overrides:

```bash
helm upgrade jentic-one \
  oci://709825985650.dkr.ecr.us-east-1.amazonaws.com/jentic/charts/jentic-one \
  --version <new-version> -n jentic-one --reset-values \
  --set global.serviceAccount.name=jentic-one \
  --set app.extraEnv.JENTIC__AUTH__CANONICAL_BASE_URL=<url>
```

Do **not** use `--reuse-values` across chart versions: it reuses the old
chart's baked defaults — including the image tag — so the pods keep running
the old version even though Helm reports the new chart deployed.

Generated secrets are never rotated by an upgrade. `helm uninstall`
intentionally keeps the `jentic-one-app-secrets` Secret so stored credentials
survive a reinstall; delete the namespace to remove everything.

## How the license check behaves

The app and broker verify your AWS Marketplace license at runtime through
the IRSA service account (AWS License Manager; the probe checks a seat out
and immediately back in, so it never consumes your contract's capacity).

- **Entitled** — nothing to see; a log line `entitlement.verdict_changed:
  entitled` at boot and a re-check every hour.
- **Not entitled** (e.g. contract expired) — the HTTP surface returns `503`
  with an RFC 9457 problem-details body (`type:
  https://jentic.com/problems/not-entitled`) on both the app **and** the
  broker, so agent traffic stops too. `/health` and `/ready` keep answering
  `200` with `{"status": "not_entitled", …}` so Kubernetes doesn't restart
  healthy pods, and `/instance` stays readable.
- **AWS unreachable** (network, throttling) — never locks you out by itself:
  the last definitive verdict holds for a 24-hour grace window.
- **Recovery** — restore the subscription and the hourly re-check unlocks the
  running pods; no reinstall or restart needed.

## Troubleshooting

| Symptom | Likely cause |
| ------- | ------------ |
| `helm registry login` or image pull returns 403 "not entitled" | Subscription not accepted yet, or still propagating (minutes); the Marketplace registry is `us-east-1` |
| `postgresql` pod `Pending`, "unbound immediate PersistentVolumeClaims" | No EBS CSI driver / default StorageClass — see Prerequisites |
| Pods don't roll after `helm upgrade --version <new>` | `--reuse-values` across versions — re-run with `--reset-values` (see Upgrades) |
| Agent approved but token exchange fails `invalid_grant` | `JENTIC__AUTH__CANONICAL_BASE_URL` unset or differs from the registered `--url` |
| Agent calls return 503 `not-entitled` | Subscription lapsed — check `/health` for `{"status": "not_entitled"}` and the subscription state in AWS Marketplace |
