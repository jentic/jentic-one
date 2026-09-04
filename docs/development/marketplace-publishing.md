# AWS Marketplace publishing

The **seller/maintainer** view of the AWS Marketplace container listing: the
entitlement gate's configuration and semantics, and the automation that
publishes images and the chart to the listing's ECR repos. Buyers should
read [`docs/installation/aws-marketplace.md`](../installation/aws-marketplace.md)
instead. The chart-side defaults live in the values overlay
[`deploy/helm/values/aws-marketplace.yaml`](../../deploy/helm/values/aws-marketplace.yaml).

**Not a Marketplace deployment? Leave `entitlement.enabled` unset — nothing
activates**, no AWS call is ever made, and the app is byte-identical to a
build without the gate.

## Entitlement check

Marketplace deployments verify their subscription with AWS at startup and
every `refresh_interval_seconds` after. Config (env or config-file — the
Marketplace values overlay carries the env form):

```yaml
entitlement:
  enabled: true
  product_code: "<product code from the Marketplace portal>"  # required when enabled
  region: "us-east-1"
  pricing_model: contract     # contract (default — the live listing) | usage
  refresh_interval_seconds: 3600
  grace_period_seconds: 86400
  # contract pricing (the live listing):
  license_sku: "<product ID from the portal>"   # NOT the product code — the
                                                # portal issues both; this is
                                                # CheckoutLicense ProductSKU
  license_dimensions: ["users", "executions"]   # the listing's dimensions;
                                                # env form takes CSV
```

Env form: `JENTIC__ENTITLEMENT__ENABLED=true`,
`JENTIC__ENTITLEMENT__PRODUCT_CODE=…`,
`JENTIC__ENTITLEMENT__LICENSE_SKU=…`,
`JENTIC__ENTITLEMENT__LICENSE_DIMENSIONS=users,executions`, etc.

**IAM**: the task role (ECS/Fargate) or IRSA role (EKS) needs, depending on
`pricing_model`:

| Pricing model | Required permission |
| ------------- | ------------------- |
| `contract` (default) | `license-manager:CheckoutLicense` (+ `license-manager:GetLicense`, `license-manager:ListReceivedLicenses` for debugging) |
| `usage` | `aws-marketplace:RegisterUsage` |

Credentials resolve from the standard runtime sources — static
`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` env, the ECS/Fargate container
credential endpoint, or EKS IRSA — no AWS SDK required in the image.

## Lockout semantics

- **Only an explicit "not entitled" answer from AWS locks the deployment
  out** — at startup or at a periodic re-check. Locked out means every
  request returns `503` (problem details,
  `type: https://jentic.com/problems/not-entitled`), except:
  - `/health`, `/<surface>/health`, `/ready` keep answering **200** with
    `{"status": "not_entitled", "reason": …}` — orchestrator probes stay
    green (the pod is healthy; the *license* isn't), and the health body is
    where an operator learns why everything else is 503.
  - `/instance` (backend identity) passes through.
- **An unreachable or erroring AWS API never locks you out by itself**: the
  last definitive verdict holds for `grace_period_seconds` (default 24h)
  before the gate fails closed.
- **Recovery needs no restart** — renewing the subscription flips the gate
  open at the next re-check.

Both the app and the broker run the gate (one image, every workload checks).

## Publishing automation

Publishing to the listing's ECR repos is automated but **dormant until two
GitHub Actions repository *variables* exist** (Settings → Secrets and
variables → Actions → Variables — variables, not secrets: none of these
values are sensitive; the trust policy below is what protects the role):

| Variable | Value |
| -------- | ----- |
| `MARKETPLACE_ECR_ROLE_ARN` | The IAM role below, e.g. `arn:aws:iam::<seller-account-id>:role/jentic-one-marketplace-publish` |
| `MARKETPLACE_ECR_IMAGE` | `709825985650.dkr.ecr.us-east-1.amazonaws.com/jentic/one-app` |
| `MARKETPLACE_ECR_POSTGRES` | `709825985650.dkr.ecr.us-east-1.amazonaws.com/jentic/one-psql` — the bundled-DB mirror of the official `postgres` image (the first-party `charts/postgresql` subchart). Needs the repo's ARN in the role policy below |
| `MARKETPLACE_ECR_CHART` | `709825985650.dkr.ecr.us-east-1.amazonaws.com/jentic/charts/jentic-one` — the Helm chart as an OCI artifact (the listing's deployment-template URI). The final path segment **must equal the chart name** (`jentic-one`): `helm push` derives it from `Chart.yaml` |

These paths belong to the current listing (`prod-cwonumew2jeyo`).

Once set:

- every release ([`release.yml`](../../.github/workflows/release.yml)
  `publish-image`) copies the **signed GHCR index byte-identically** into the
  Marketplace repo (same digest — asserted) and cosign-signs the ECR
  reference too;
- [`marketplace-mirror.yml`](../../.github/workflows/marketplace-mirror.yml)
  (weekly + manual dispatch) mirrors the bundled Postgres image
  ([`postgres-mirror.Dockerfile`](../../deploy/docker/postgres-mirror.Dockerfile),
  digest kept fresh by Dependabot) through the same Trivy gate;
- [`marketplace-chart.yml`](../../.github/workflows/marketplace-chart.yml)
  publishes the umbrella Helm chart as an OCI artifact after every successful
  release run (chart version = tag minus the `v`). The packaged chart is NOT
  byte-identical to the repo chart: the workflow **bakes
  [`aws-marketplace.yaml`](../../deploy/helm/values/aws-marketplace.yaml) +
  the release version (`X.Y.Z`, no `v` — the tag the images carry in ECR)
  into its `values.yaml`** first — AWS requires image references to live in
  the chart's own defaults (their validator extracts them there, and their
  replication pipeline rewrites them per region). The publish gate then runs
  exactly what AWS runs on submission: bare `helm lint` + bare
  `helm template` (must succeed — the baked chart generates every secret, so
  no install-time password guard remains in its render) with every rendered
  image from the listing's ECR. Backfill an already-released tag with
  `gh workflow run marketplace-chart.yml -f tag=vX.Y.Z`.

Publishing a new **listing version** stays a portal step (product → Request
changes → *Add version*, pinning the pushed tag/digest); automate via the
Marketplace Catalog API only after the manual loop has worked once.

### Listing override parameters

Because the Marketplace chart is self-contained — every password and secret
is generated at install
([Secrets](../installation/helm.md#secrets)) — the version's **Helm delivery
option** needs only two override parameters, both substituted by AWS at
launch (portal validation rejects paid products without them). A buyer
supplies nothing:

| Override parameter key | DefaultValue |
| ---------------------- | ------------ |
| `global.serviceAccount.name` | `${AWSMP_SERVICE_ACCOUNT}` — the buyer's (IRSA) service account; the app/broker pods run under it (chart support: `global.serviceAccount`) |
| `global.awsmp.licenseSecret` | `${AWSMP_LICENSE_SECRET}` — an AWS-created Secret, mounted read-only at `/var/run/secrets/aws-marketplace/license` (chart support: `global.awsmp.licenseSecret`) |

Everything else (ECR image repositories, `broker.enabled=true`, the bundled
Postgres, the secret/password generation, the tag pin) is baked into the
published chart's defaults. Buyers preferring RDS install manually instead,
disabling the bundled DB and setting the `global.databases.*.host` values
plus explicit `*.password` values (explicit passwords always win over the
generated ones).

### IAM role (seller account)

The role trusts GitHub's OIDC provider — no long-lived AWS keys anywhere.
Trust policy (create the `token.actions.githubusercontent.com` OIDC provider
first if the account doesn't have it):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<seller-account-id>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:jentic/jentic-one:ref:refs/tags/v*"
        }
      }
    },
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<seller-account-id>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
          "token.actions.githubusercontent.com:sub": "repo:jentic/jentic-one:ref:refs/heads/main"
        }
      }
    }
  ]
}
```

(The first statement covers the release workflow, which runs on `v*` tags;
the second covers the mirror and chart workflows, which run on `main` —
`workflow_run`/scheduled/dispatched workflows execute on the default branch.)

Permissions policy — ECR push scoped to the listing's repos, which live in
AWS's Marketplace registry account and are granted to the seller through the
portal (`aws-marketplace` actions may be required by newer portal setups; add
`"aws-marketplace:*ChangeSet*"` only when automating Add version):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "ecr:GetAuthorizationToken",
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:CompleteLayerUpload",
        "ecr:InitiateLayerUpload",
        "ecr:PutImage",
        "ecr:UploadLayerPart",
        "ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer",
        "ecr:DescribeImages"
      ],
      "Resource": [
        "arn:aws:ecr:us-east-1:709825985650:repository/jentic/one-app",
        "arn:aws:ecr:us-east-1:709825985650:repository/jentic/one-psql",
        "arn:aws:ecr:us-east-1:709825985650:repository/jentic/charts/jentic-one"
      ]
    }
  ]
}
```
