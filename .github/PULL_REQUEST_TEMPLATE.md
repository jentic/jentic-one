<!--
Thanks for contributing to Jentic One!
Keep PRs focused and reviewable. Do not include secrets or credentials.
Match the depth to the change: a typo fix needs one Summary sentence;
a structural change needs every section (and ideally a diagram).
-->

## Summary

<!-- 1-3 sentences: the problem and why this change is the fix. Written for
     a reader who has NOT seen the diff — lead with the impact, not the
     mechanics. -->

## Changes

<!-- One bullet per meaningful change, most important first. Name the surface
     (`control`, `ui`, `cli`, `deploy`, …). Say what is deliberately OUT of
     scope. For topology/flow changes, a ```mermaid``` block renders here. -->

-

## Risk & rollback

<!-- Required when the change warrants it: breaking changes, migrations,
     auth/credential/permission surface, new config/env vars — each on its
     own line. How to revert. If genuinely low-risk: "Low risk: <reason>". -->

## Test plan

<!-- How this was verified: commands run (`make check`, e2e), or manual steps
     a reviewer can reproduce. UI changes: screenshot or short recording.
     If no automated tests: "No automated tests; manual verification: …" -->

## Review guide

<!-- Optional, for larger PRs: where to start reading, in what order, what to
     scrutinize. Delete if not needed. -->

## Related issue

<!-- e.g. Closes #123 -->

## Checklist

- [ ] Commits follow Conventional Commits with a scope and are signed off (`git commit -s`, DCO)
- [ ] `make check` passes (lint, type check, secrets audit, arch tests)
- [ ] Tests added/updated for the change
- [ ] Docs updated where relevant
- [ ] No secrets, credentials, or internal-only references included
