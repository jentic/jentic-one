# Spike 3 — which commands escape the endpoint-override wiring?

Date 2026-10-19. Image: `Dockerfile` (spike-1 image + AWS CLI 2.34.63; the zip
sha256 is recorded at `/awscli.sha256` on first download, because AWS ships a
PGP signature rather than a sha256 sum). The container runs with
`--network none`, and an in-container recording mock (`mock.py`) is reachable
only through `HTTP_PROXY`. So any call that doesn't use the loopback fails
with a connection error. Commands are in `run.sh`; raw output is in
`results-raw.txt`.

Wiring under test (per-CLI endpoint overrides):

- **gh:** `GH_HOST=github.localhost` + `HTTP_PROXY`
- **aws:** `AWS_ENDPOINT_URL_S3/STS=http://<svc>.eu-west-1.amazonaws.com` + `HTTP_PROXY`

## Results

| Command | Outcome | Calls via loopback | Escaped to |
|---|---|---|---|
| `gh pr list -R o/r` | ✅ exit 0 | 1 (`POST /graphql`) | — |
| `gh pr view https://github.com/o/r/pull/1` | ❌ exit 1 | 0 | `api.github.com`. An absolute URL argument switches the host |
| `gh api --paginate /repos/o/r/issues` | ❌ exit 1, after page 1 printed | 1 | `api.github.com`. gh follows the absolute `Link: rel="next"` |
| `gh run view 1 --log -R o/r` | ❌ exit 1 | 4 | `productionresultssa0.blob.core.windows.net` (the 302 target of `/logs`) |
| `gh release download v1 -R o/r` | ❌ exit 1 | 2 | `api.github.com` (asset `url` field) |
| `aws s3 ls` | ✅ | 1 | — |
| `aws s3 ls s3://my-bucket` | ✅ | 1, path-style (`/my-bucket?list-type=2…`), so no bucket host | — |
| `aws s3 ls s3://my-bucket --region us-east-1` | ⚠️ exit 0, **wrong region** | 1, to the eu-west-1 endpoint | — (the `--region` flag is silently ignored) |
| `aws ec2 describe-regions` | ❌ exit 255 | 0 | `https://ec2.eu-west-1.amazonaws.com/` (no endpoint var for this service) |
| `aws s3 cp` 2 MB | ✅ | 1 PUT, `x-amz-content-sha256` = full payload hash, no `aws-chunked` | — |
| `aws s3api put-object` 2 MB | ✅ | same | — |

## What this means

1. **Per-CLI endpoint overrides aren't enough.** 4 of the 5 non-trivial `gh`
   commands escape. The hosts come from **response data** (pagination `Link`
   headers, redirect `Location`s, asset URLs) and **user arguments**
   (absolute URLs), not only from configuration. Any working design has to
   route *every* outbound connection the CLI makes. A fixed list of base URLs
   won't do it.
2. **The hosts the CLI follows from responses are a real, mixed set.** They
   include `api.github.com`, Azure blob storage for logs, and (from the
   asset redirect) `objects.githubusercontent.com`. Several are pre-signed
   download hosts, which raises questions about redirect handling and
   credential-bearing URLs. Each profile needs an explicit list of allowed
   download hosts.
3. **Per-service AWS endpoint variables cause silent wrong-region results.**
   That's worse than a failure. Every service not listed fails outright.
4. **Over an `http://` endpoint, the AWS CLI signs the full payload** (no
   streaming chunk signatures), at least up to 2 MB here. Re-signing at the
   broker is therefore a plain SigV4 re-sign for these cases. Behaviour over
   `https`, multipart uploads (>8 MB default threshold) and the newer
   checksum trailers is **not tested** yet.
5. **The good news:** with the right routing, the commands themselves work
   unchanged without a local repo when `-R` is given. The CLI's network
   behaviour is fully observable at the loopback.

Whatever replaces this wiring should be tested with this same
matrix (`run.sh`) before adopting it.
