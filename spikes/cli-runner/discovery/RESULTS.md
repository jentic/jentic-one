# Spike 2 — can the registry route the calls a broker-side CLI makes?

Date 2026-10-19. The test is
`tests/integration/registry/ingest/test_spike_cli_runner_discovery.py`. It
uses the real `Ingestor` → `RevisionService.promote` → the broker's
`discover()` via `InProcessRegistryResolver`, on SQLite
(`JENTIC_TEST_BACKEND=sqlite`), with no Docker and no shared fixtures.

Run it with:

```
JENTIC_TEST_BACKEND=sqlite uv run pytest tests/integration/registry/ingest/test_spike_cli_runner_discovery.py -q -s --no-cov
```

## Results

| Case | Result |
|---|---|
| GitHub `POST https://api.github.com/graphql`, as its own API (`github.com/graphql`) | ✅ resolves to `/graphql` POST |
| GitHub REST `GET /repos/cli/cli/pulls` (`github.com/rest`) on the **same host** | ✅ resolves, with `owner`/`repo` path params |
| `GET /repos/cli/cli/readme`, which isn't in any spec | ✅ `None` (the broker would 404) |
| Ingested but **not promoted** revision | `None`. Only `apis.current_revision_id` is routed |
| S3 path-style `https://s3.amazonaws.com/my-bucket` | ✅ |
| S3 `https://s3.{region}.amazonaws.com` with `region` default only | ✅ for the **default** region only. `us-east-1` → `None` |
| S3 `{region}` with `enum: [eu-west-1, us-east-1, ap-southeast-2]` | ✅ each listed region. `sa-east-1` → `None` |
| S3 virtual-host `https://{bucket}.s3.{region}…` | ✅ only for the default `bucket` value. A server variable without an `enum` expands to its default (plus a literal `{name}` row), **not** a wildcard. See the index rows the test prints |

## What this means

1. **GitHub coverage is a packaging problem, not an engine problem.**
   - A `POST /graphql` operation next to the REST spec routes correctly
     today.
   - The gap is that nothing ships these specs. Each profile must bring its
     operations and **promote** them.
   - Calls the CLI makes that aren't in the shipped spec still 404. For
     `gh` that includes whatever REST paths the pinned version uses. The
     wiring spike's recorded calls are the input for that list.
2. **One host can only be live under one vendor**
   (`tests/integration/registry/services/test_url_index_live_revisions.py`).
   - A profile's shipped GitHub operations and an operator's own GitHub
     import must agree on the vendor.
   - Otherwise one of them can't be promoted.
   - The design has to say which side owns the host.
3. **AWS regions are representable** as a server-variable `enum`, which is
   finite. Each enum value becomes an index row, so this has a cost
   (services × regions × operations).
4. **S3 virtual-host buckets aren't representable.** Bucket names are
   unbounded, and the index has no wildcard variables. Options:
   - force path-style addressing in the runner's AWS config. This needs
     checking against AWS's path-style support for new buckets;
   - or add wildcard server variables to the URL index. That's an engine
     change, with `server_variables` scoping implications.
5. **Each matched call carries its `server_variables`**, e.g.
   `{'region': 'eu-west-1'}`. That's exactly what a per-request SigV4
   service/region derivation needs.
