# Security: retire the orphaned `jentic-mini` GHCR package (P0)

> **STATUS: security action — needs an org owner.** Split out of the release-procedure
> proposal because it is a live exposure independent of the release work, and is blocked
> on access the working token doesn't have.

## The exposure (live today)

`ghcr.io/jentic/jentic-mini` is **public and anonymously pullable** — verified: an anon
token + `GET /v2/jentic/jentic-mini/manifests/latest` returns **HTTP 200**. Tags run the
**full `0.2.0`…`0.13.2`** line plus `latest`, `unstable`, and some `pr-*` tags. These
images **predate the OSS scrub**, so any content later removed from the public repos may
still be baked into their layers and pullable by anyone, right now.

For a **credential broker**, the blast radius is specifically: the credential-at-rest
**encryption keyset**, the `ARAZZO_BUILDER_APP_ID` App private key, GHCR/registry push
tokens, DB credentials, and any provider API keys ever baked into old compose/Dockerfiles.

## The one rule

**Rotation is the only fix. Deletion is cosmetic.** Deleting the package stops *future*
anonymous pulls, but anyone who already pulled (or CI caches, laptops, backups) keeps a
copy forever, and GHCR's 30-day soft-delete keeps it restorable meanwhile. So the exposure
is only actually closed by rotating every secret that ever touched those layers.

## Actions (in order)

1. **Inventory + rotate every candidate secret (P0, do first, non-negotiable):**
   audit the image layers (`docker history --no-trunc`, `dive`) **and** run a
   **full git-history secret scan** (gitleaks / trufflehog in `git` mode, over *all refs*)
   across **all four repos**: `jentic-mini`, `jentic-one`, `jentic-one-internal`,
   `jentic-one-opensourceable`. (`detect-secrets` in the repo is a *pre-commit* hook — it
   protects new commits only, not pre-scrub history.) Rotate/revoke each secret class
   above. Get explicit sign-off that each was rotated.
2. **Check for other orphaned packages** under the `-internal` / `-opensourceable` orgs/repos
   (same anon-pull probe), in case the scrub split content across them.
3. **(Optional, short) grace:** flip the package **Private** for a few days to catch
   surprise consumers (public→private is UI-only). Skip to close exposure immediately.
4. **Delete the whole package:** UI Danger Zone → Delete, or
   `DELETE /orgs/jentic/packages/container/jentic-mini` with a `read:packages` +
   `delete:packages` token (used once by a human, then revoked — not stored as a workflow
   secret). 30-day restore is the safety net.
5. **Rename note** in the `jentic-one` README ("formerly `jentic-mini`; old images removed —
   use `ghcr.io/jentic/jentic-one`"). **Do not republish under the old name.**

## Blocked on

The working token has `gist, read:org, repo` only — it **cannot** list/delete packages or
run the org-wide history scan. Needs an **org owner** (GHCR UI) or a token with
`read:packages` + `delete:packages`, plus repo-admin for the history scans. **Escalate now**
— #1 (rotation) is the time-sensitive part and shouldn't wait on the deletion mechanics.

## References

- GHCR delete/restore (scopes, 30-day window): <https://docs.github.com/en/packages/learn-github-packages/deleting-and-restoring-a-package>
- Secrets in image layers → rotate (deleting doesn't un-leak): <https://trufflesecurity.com/blog/how-secrets-leak-out-of-docker-images>
- GitHub "remove sensitive data → rotate the secret first": <https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository>
