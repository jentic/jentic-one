# Changelog

## [0.33.0](https://github.com/jentic/jentic-one/compare/v0.32.1...v0.33.0) (2026-08-27)


### Features

* **ci:** publish the Helm chart to Marketplace ECR as an OCI artifact ([#1145](https://github.com/jentic/jentic-one/issues/1145)) ([c15eab8](https://github.com/jentic/jentic-one/commit/c15eab8f494f58d7802e9a3e6c159d1212447642))
* **helm:** first-party bundled Postgres on the official image ([#1150](https://github.com/jentic/jentic-one/issues/1150)) ([2005f18](https://github.com/jentic/jentic-one/commit/2005f184d7bed414f47049192c83c9e06001d32b))
* **helm:** make the Marketplace listing RDS-only, park the postgres mirror ([#1143](https://github.com/jentic/jentic-one/issues/1143)) ([7e4b244](https://github.com/jentic/jentic-one/commit/7e4b244f1441b37eed58543e07030b4b6f3b0f72))
* **helm:** Marketplace launch wiring — service account + license secret ([#1149](https://github.com/jentic/jentic-one/issues/1149)) ([e55318e](https://github.com/jentic/jentic-one/commit/e55318e457811817b87dec9ec3c18ba70e77b6b7))


### Bug Fixes

* **ci:** docker login before cosign signs the Marketplace chart ([#1148](https://github.com/jentic/jentic-one/issues/1148)) ([ef56ec6](https://github.com/jentic/jentic-one/commit/ef56ec6e38507a72cb9988c7769edac0550706c6))

## [0.32.1](https://github.com/jentic/jentic-one/compare/v0.32.0...v0.32.1) (2026-08-26)


### Bug Fixes

* **auth:** re-check actor status on every token verdict so disable kills outstanding tokens ([#1137](https://github.com/jentic/jentic-one/issues/1137)) ([4dd7acd](https://github.com/jentic/jentic-one/commit/4dd7acd456a68c01faccfd2556a35d8cf351897a))

## [0.32.0](https://github.com/jentic/jentic-one/compare/v0.31.1...v0.32.0) (2026-08-25)


### Features

* AWS Marketplace entitlement gate (plan PRs 3+4) ([#1041](https://github.com/jentic/jentic-one/issues/1041)) ([114e456](https://github.com/jentic/jentic-one/commit/114e456f258da1e4534548efadc93ec41b73104d))
* **cli:** CLI V2 rebuild ([#1049](https://github.com/jentic/jentic-one/issues/1049)) ([#1094](https://github.com/jentic/jentic-one/issues/1094)) ([4e44f67](https://github.com/jentic/jentic-one/commit/4e44f677befedaacf644ca94174a6736b63f27e1))
* **telemetry:** report OS family per boot on instance_booted ([#1101](https://github.com/jentic/jentic-one/issues/1101)) ([2c392ad](https://github.com/jentic/jentic-one/commit/2c392ad04adb988da5ded7c922d21e680ba27c34))


### Bug Fixes

* **install:** build the server from source for a non-release Docker install ([#1093](https://github.com/jentic/jentic-one/issues/1093)) ([0acd0a8](https://github.com/jentic/jentic-one/commit/0acd0a82cc33022c7e71762c5521c9e2ffffa7ba))
* **registry:** accept canonical vendor/name/version slugs in search api filters ([#1083](https://github.com/jentic/jentic-one/issues/1083)) ([af5c094](https://github.com/jentic/jentic-one/commit/af5c094f711bff263399c1e0d8a92d25194d18e6)), closes [#1080](https://github.com/jentic/jentic-one/issues/1080)
* **registry:** make trailing-slash paths matchable in the broker URL index ([#1096](https://github.com/jentic/jentic-one/issues/1096)) ([8b83d6d](https://github.com/jentic/jentic-one/commit/8b83d6d435f8a12781aaad1d09a31dd16e9cbb49)), closes [#1085](https://github.com/jentic/jentic-one/issues/1085)


### Build System

* **deps-dev:** bump @testing-library/user-event ([#1103](https://github.com/jentic/jentic-one/issues/1103)) ([4f7af70](https://github.com/jentic/jentic-one/commit/4f7af7069aa3671b984616db9abd4d07fbb98c12))
* **deps-dev:** bump @types/pg ([#1104](https://github.com/jentic/jentic-one/issues/1104)) ([dcb9450](https://github.com/jentic/jentic-one/commit/dcb9450b2a22854a0a737eacd12704c6f414e74c))
* **deps-dev:** bump the eslint group across 1 directory with 5 updates ([#1072](https://github.com/jentic/jentic-one/issues/1072)) ([3ab5469](https://github.com/jentic/jentic-one/commit/3ab54693ca1479c6659e40e1fe1b006a632664d4))
* **deps-dev:** bump the vite group in /ui with 4 updates ([#1102](https://github.com/jentic/jentic-one/issues/1102)) ([4edfa3b](https://github.com/jentic/jentic-one/commit/4edfa3b0235fd17b282103e415dafa4793279c3e))
* **deps:** bump lucide-react from 1.31.0 to 1.32.0 in /ui ([#1105](https://github.com/jentic/jentic-one/issues/1105)) ([3f21e2a](https://github.com/jentic/jentic-one/commit/3f21e2a0c5174a8c4b826c555165c203189e58cc))
* **deps:** bump the python group with 19 updates ([#1107](https://github.com/jentic/jentic-one/issues/1107)) ([1d6a5c3](https://github.com/jentic/jentic-one/commit/1d6a5c39c0835a50e95288f7bbb29cb62ad2321c))

## [0.31.1](https://github.com/jentic/jentic-one/compare/v0.31.0...v0.31.1) (2026-08-18)


### Bug Fixes

* **docs:** repair the agent onboarding front door and add a drift guard ([#1071](https://github.com/jentic/jentic-one/issues/1071)) ([148b122](https://github.com/jentic/jentic-one/commit/148b122f8adebf5b61ba44c516c5977d4399e95f))
* **registry:** Flow-3 concurrency follow-ups — A4b supersede target + notify durability ([#940](https://github.com/jentic/jentic-one/issues/940), [#941](https://github.com/jentic/jentic-one/issues/941)) ([#1048](https://github.com/jentic/jentic-one/issues/1048)) ([535d1e3](https://github.com/jentic/jentic-one/commit/535d1e3c15d3873aa55368ca0c54035b7a23b742))
* **registry:** rollback base state-fidelity ([#939](https://github.com/jentic/jentic-one/issues/939)) + auth layering guard ([#938](https://github.com/jentic/jentic-one/issues/938)) ([#1047](https://github.com/jentic/jentic-one/issues/1047)) ([a7f8b9b](https://github.com/jentic/jentic-one/commit/a7f8b9b40252613ac1127ca02b00c6e9983fa245))
* **security:** patch util-linux CVE-2026-53615 in the app image base ([#1060](https://github.com/jentic/jentic-one/issues/1060)) ([7b212e2](https://github.com/jentic/jentic-one/commit/7b212e2ed8e7d4349b166d2f706c36ede3e74a5e))


### Refactors

* **registry:** search-strategy shadowing guard ([#958](https://github.com/jentic/jentic-one/issues/958)) + sha-less spec_digest collision ([#780](https://github.com/jentic/jentic-one/issues/780)) ([#1045](https://github.com/jentic/jentic-one/issues/1045)) ([9eaec98](https://github.com/jentic/jentic-one/commit/9eaec98880ebc7e006cb607b10b9a5098a1d587e))


### Documentation

* add llms.txt, and an install-and-use section to AGENTS.md ([#1052](https://github.com/jentic/jentic-one/issues/1052)) ([087b800](https://github.com/jentic/jentic-one/commit/087b800c8aafaca1c57462440b4cd2b156b149f0))
* **readme:** fix first-run admin flow guides ([#1068](https://github.com/jentic/jentic-one/issues/1068)) ([96c4e7b](https://github.com/jentic/jentic-one/commit/96c4e7b92053367367e1b8af6de5c05dffa30bf2))
* **readme:** rewrite the front door for discovery and first-run success ([#1051](https://github.com/jentic/jentic-one/issues/1051)) ([59eff46](https://github.com/jentic/jentic-one/commit/59eff465f0bd772d4d40ac64932f346604d6c44f))


### Build System

* **deps-dev:** bump @testing-library/user-event ([#1073](https://github.com/jentic/jentic-one/issues/1073)) ([91c6445](https://github.com/jentic/jentic-one/commit/91c6445eb0547320708c2e4f57b44a17de196c78))
* **deps-dev:** bump globals from 17.9.0 to 17.11.0 in /ui ([#1074](https://github.com/jentic/jentic-one/issues/1074)) ([5f9f07f](https://github.com/jentic/jentic-one/commit/5f9f07fbd7ee60e904dac1021012337d740e150a))

## [0.31.0](https://github.com/jentic/jentic-one/compare/v0.30.3...v0.31.0) (2026-08-14)


### Features

* **auth:** add agent ownership-claim primitive ([#1042](https://github.com/jentic/jentic-one/issues/1042)) ([6b5337a](https://github.com/jentic/jentic-one/commit/6b5337a7749f78b5f84e11f21ff8277069ec1d06))
* **helm:** AWS Marketplace values + required DB password guards ([#1039](https://github.com/jentic/jentic-one/issues/1039)) ([7e26cea](https://github.com/jentic/jentic-one/commit/7e26cea541ea9521a5b36d338a444b6c862806de))

## [0.30.3](https://github.com/jentic/jentic-one/compare/v0.30.2...v0.30.3) (2026-08-12)


### Bug Fixes

* **broker:** gate background jobs on enabled apps, not DB presence ([#1028](https://github.com/jentic/jentic-one/issues/1028)) ([b6cf312](https://github.com/jentic/jentic-one/commit/b6cf3122b21d333f46615277ad480023757f33c0))

## [0.30.2](https://github.com/jentic/jentic-one/compare/v0.30.1...v0.30.2) (2026-08-12)


### Bug Fixes

* **auth:** build OIDC callback URI from canonical base URL ([#1026](https://github.com/jentic/jentic-one/issues/1026)) ([1c2934b](https://github.com/jentic/jentic-one/commit/1c2934b7736b47c9084e0a412e403fae77736956))

## [0.30.1](https://github.com/jentic/jentic-one/compare/v0.30.0...v0.30.1) (2026-08-12)


### Bug Fixes

* **config:** coerce indexed env vars into lists ([#1023](https://github.com/jentic/jentic-one/issues/1023)) ([a4bc4fb](https://github.com/jentic/jentic-one/commit/a4bc4fb1f41f41b2595eb8c3c4014f5ce28fe5aa))

## [0.30.0](https://github.com/jentic/jentic-one/compare/v0.29.1...v0.30.0) (2026-08-11)


### Features

* **auth:** add SSO login seams — Google provider, provisioning hook, superset verifier ([#1021](https://github.com/jentic/jentic-one/issues/1021)) ([2795a05](https://github.com/jentic/jentic-one/commit/2795a0534ccb86ba863a80e34d6978dc259b0b6d))

## [0.29.1](https://github.com/jentic/jentic-one/compare/v0.29.0...v0.29.1) (2026-08-10)


### Bug Fixes

* **reference:** key prefixed included-router routes by their full path ([#1018](https://github.com/jentic/jentic-one/issues/1018)) ([9805994](https://github.com/jentic/jentic-one/commit/9805994cecb7e1bf1308311d2cc673a45a8622b9))

## [0.29.0](https://github.com/jentic/jentic-one/compare/v0.28.1...v0.29.0) (2026-08-07)


### Features

* **workspace:** make API revisions & overlays legible in the workspace UI ([#1002](https://github.com/jentic/jentic-one/issues/1002)) ([5b011f0](https://github.com/jentic/jentic-one/commit/5b011f09d22c62239551005aca7877c44818de4f))


### Bug Fixes

* **ingest:** keep bare YAML dates as strings so specs stay JSON-serializable ([#983](https://github.com/jentic/jentic-one/issues/983)) ([42a8f38](https://github.com/jentic/jentic-one/commit/42a8f384ca2c761e2758efd80af7f63ee072f31d))
* **ingest:** keep non-finite YAML/JSON floats as strings so specs stay JSON-serializable ([#987](https://github.com/jentic/jentic-one/issues/987)) ([5bd79cd](https://github.com/jentic/jentic-one/commit/5bd79cd3d13c8eae03719ee4cc6014c452ffcbe6))
* **ingest:** wrap parser escapes so malformed spec content fails cleanly ([#989](https://github.com/jentic/jentic-one/issues/989)) ([bc48601](https://github.com/jentic/jentic-one/commit/bc486011cbb60993a835d4b693678d28a0fd9f44))

## [0.28.1](https://github.com/jentic/jentic-one/compare/v0.28.0...v0.28.1) (2026-08-06)


### Bug Fixes

* **install.sh:** make the piped re-exec source URL overridable ([#972](https://github.com/jentic/jentic-one/issues/972)) ([e124104](https://github.com/jentic/jentic-one/commit/e124104bee75825e511a29411b7361ecd52d6e69))
* **skills:** sync [#911](https://github.com/jentic/jentic-one/issues/911) reuse guidance into the jentic skill source ([#977](https://github.com/jentic/jentic-one/issues/977)) ([0c153b8](https://github.com/jentic/jentic-one/commit/0c153b8f4b1825ea8644b75a530ca7d4df3db20a))

## [0.28.0](https://github.com/jentic/jentic-one/compare/v0.27.0...v0.28.0) (2026-08-06)


### Features

* **access-requests:** surface existing-toolkit reuse on the provision path ([#897](https://github.com/jentic/jentic-one/issues/897)) ([#911](https://github.com/jentic/jentic-one/issues/911)) ([6136c24](https://github.com/jentic/jentic-one/commit/6136c24df53eef0dab5e2c3a2ea11517e08126ea))
* **app:** in-app update banner for new releases ([#964](https://github.com/jentic/jentic-one/issues/964)) ([322a7ef](https://github.com/jentic/jentic-one/commit/322a7ef63e8b7127f7178ac20302f52e6366fd02))


### Documentation

* **.github:** align the PR template with the shared description convention ([#968](https://github.com/jentic/jentic-one/issues/968)) ([f94bc3e](https://github.com/jentic/jentic-one/commit/f94bc3e73562fe9b815ec79a0cf9e32fb35b3731))
* **plans:** move implementation plans to the jentic-one-plans repo ([#967](https://github.com/jentic/jentic-one/issues/967)) ([7095f73](https://github.com/jentic/jentic-one/commit/7095f7315e026605409ec49266827d3b85056849))

## [0.27.0](https://github.com/jentic/jentic-one/compare/v0.26.0...v0.27.0) (2026-08-05)


### Features

* **cli:** make jentic run launch Codex, Cursor, and Hermes as isolated agents ([#935](https://github.com/jentic/jentic-one/issues/935)) ([d05f6c7](https://github.com/jentic/jentic-one/commit/d05f6c71c61ecbd8f929867cf9c60bb426e1d061))
* **cli:** run local coding agents as a dedicated unix user ([#853](https://github.com/jentic/jentic-one/issues/853)) ([8052479](https://github.com/jentic/jentic-one/commit/805247989b02d264fd69b100376dbae2c3ba0602))
* **credentials:** add AWS SigV4 credential type ([#776](https://github.com/jentic/jentic-one/issues/776)) ([#888](https://github.com/jentic/jentic-one/issues/888)) ([a11025a](https://github.com/jentic/jentic-one/commit/a11025a5981196a8125ed2b9b1d082c8c9498609))
* **skills:** distribute a served skill set to agents ([#966](https://github.com/jentic/jentic-one/issues/966)) ([49345b7](https://github.com/jentic/jentic-one/commit/49345b7aa81b62821e9f8510e377c710584b9799))

## [0.26.0](https://github.com/jentic/jentic-one/compare/v0.25.0...v0.26.0) (2026-08-04)


### Features

* **cli:** guard docker-backed commands against a stopped daemon ([#942](https://github.com/jentic/jentic-one/issues/942)) ([7ddbb09](https://github.com/jentic/jentic-one/commit/7ddbb09cb260147ce7b846ae0c557083a9950b00))
* **flow3:** close the overlay-update reconciliation loop ([#937](https://github.com/jentic/jentic-one/issues/937)) ([d24dcd7](https://github.com/jentic/jentic-one/commit/d24dcd79674991016b3459e73f470f4d3aee5784))
* **flow3:** jitter the catalog update-sweep interval to de-phase replicas ([#917](https://github.com/jentic/jentic-one/issues/917)) ([6c5b755](https://github.com/jentic/jentic-one/commit/6c5b755f46bc162888934553162b318ed46e3805))
* **flow3:** overlay-loop legibility, hygiene & lifecycle follow-ups ([#955](https://github.com/jentic/jentic-one/issues/955)) ([8b33d88](https://github.com/jentic/jentic-one/commit/8b33d88d2b7f0e6ef254599f0b5f0a9268490bb5))
* **flow3:** standalone catalog-update scanner + update-available surfaces ([#912](https://github.com/jentic/jentic-one/issues/912)) ([c152bb6](https://github.com/jentic/jentic-one/commit/c152bb660258f1374f27b040a60671b5a2f9617e))
* **overlays:** persist superseded_revision_id at materialize time (A5a) ([#918](https://github.com/jentic/jentic-one/issues/918)) ([95b8c14](https://github.com/jentic/jentic-one/commit/95b8c14a8d9da907d38007e6fb0828a4514771a9))
* **overlays:** purpose-scoped overlays:confirm gate ([#916](https://github.com/jentic/jentic-one/issues/916)) ([cc7b218](https://github.com/jentic/jentic-one/commit/cc7b2184e931fa1d99c8332734f7891d00a26b08))
* **overlays:** re-materialize a confirmed overlay on edit (D1, [#927](https://github.com/jentic/jentic-one/issues/927)) ([#956](https://github.com/jentic/jentic-one/issues/956)) ([2e11149](https://github.com/jentic/jentic-one/commit/2e11149677d3aa2e685a4f2affb04883a4e1b6a3))
* persist catalog identity (api_id) and title API surfaces from it ([#852](https://github.com/jentic/jentic-one/issues/852)) ([73cb558](https://github.com/jentic/jentic-one/commit/73cb558a171c0727412390794b1f6e75f821c3be))
* **seams:** add register_pipeline_stage — ingest pipeline extension seam ([#957](https://github.com/jentic/jentic-one/issues/957)) ([d1472c9](https://github.com/jentic/jentic-one/commit/d1472c95dc996b5db2d94969fe717ee1cd314055))


### Bug Fixes

* **cli:** distinguish "docker not installed" from a stopped daemon ([#961](https://github.com/jentic/jentic-one/issues/961)) ([2d2da92](https://github.com/jentic/jentic-one/commit/2d2da92124fec93c2938896346deef599a893092)), closes [#954](https://github.com/jentic/jentic-one/issues/954)
* **cli:** gate the stack update on its own recorded ref ([#944](https://github.com/jentic/jentic-one/issues/944)) ([6de0631](https://github.com/jentic/jentic-one/commit/6de0631c17fd6d0a11fc382e8cc461772b57fff6))
* **cli:** honor --ref when building the stack ([#950](https://github.com/jentic/jentic-one/issues/950)) ([abbe0a8](https://github.com/jentic/jentic-one/commit/abbe0a85139539a8679c531d499a2c01d565892a))
* **cli:** let Ctrl-C cancel the Docker-daemon probe's cold-start wait ([#960](https://github.com/jentic/jentic-one/issues/960)) ([a239f78](https://github.com/jentic/jentic-one/commit/a239f78208c3a5f68e414c3bb3f1b3d32ef42ea3)), closes [#953](https://github.com/jentic/jentic-one/issues/953)
* **cli:** stop `start` coming up on an unmigrated database ([#952](https://github.com/jentic/jentic-one/issues/952)) ([0526a10](https://github.com/jentic/jentic-one/commit/0526a105aec8f2aaa289a368cd8882f2a71ecd98))
* **monitoring:** include the current partial minute in usage aggregates ([#915](https://github.com/jentic/jentic-one/issues/915)) ([e414d1c](https://github.com/jentic/jentic-one/commit/e414d1ca3bbabe7da2e8f80e7612fc0c7359bef5))
* **web:** revalidate the SPA shell and cache hashed assets immutably ([#946](https://github.com/jentic/jentic-one/issues/946)) ([8fdbc2f](https://github.com/jentic/jentic-one/commit/8fdbc2fcd8bac95b928d7a00720e9ec5b41bad6f))

## [0.25.0](https://github.com/jentic/jentic-one/compare/v0.24.0...v0.25.0) (2026-07-31)


### Features

* act on access-request satisfaction hints across reviewer and fulfilment surfaces ([#902](https://github.com/jentic/jentic-one/issues/902)) ([c86f4d1](https://github.com/jentic/jentic-one/commit/c86f4d1c8c95ee9452f2a0f19c1525a6f2521aca))
* **catalog:** notify when a registered API's upstream spec changes (Flow 3 MVP) ([#893](https://github.com/jentic/jentic-one/issues/893)) ([a042885](https://github.com/jentic/jentic-one/commit/a04288504925708c58064f257c9ecc3c7b175008))
* **overlay:** materialize confirmed overlays onto the served spec ([#904](https://github.com/jentic/jentic-one/issues/904)) ([2964069](https://github.com/jentic/jentic-one/commit/2964069e31826b538b3a12b716da72da524c6d63))
* **ui:** rail day separators, proactive failure surfacing, and monitor event drill-in ([#873](https://github.com/jentic/jentic-one/issues/873)) ([db65dd8](https://github.com/jentic/jentic-one/commit/db65dd8a22301dc1ad9a91ec6df03a9274d2d8e6))


### Bug Fixes

* **broker:** derive a valid trace_id at the execute edge instead of raw headers ([#905](https://github.com/jentic/jentic-one/issues/905)) ([eff4d7c](https://github.com/jentic/jentic-one/commit/eff4d7cc5d969f8a53acd94588bb41bc46ffba9a))
* **catalog:** rank whole-word api_id matches above substring matches ([#872](https://github.com/jentic/jentic-one/issues/872)) ([46a919a](https://github.com/jentic/jentic-one/commit/46a919a9fda4b2a10a4959d8a032518f5cdd9ef5))
* **install.sh:** default to the latest release tag, not main ([#909](https://github.com/jentic/jentic-one/issues/909)) ([741854f](https://github.com/jentic/jentic-one/commit/741854f57477b910284b8ee05d011803cddfbf53)), closes [#908](https://github.com/jentic/jentic-one/issues/908)
* **ui:** reset catalog scroll to top on new search or filter ([#850](https://github.com/jentic/jentic-one/issues/850)) ([7303a1c](https://github.com/jentic/jentic-one/commit/7303a1c0cf83f6150caad3aff721c62334156824))


### Refactors

* **ui:** share the detail-console grammar across toolkit, agent, and SA consoles ([718da62](https://github.com/jentic/jentic-one/commit/718da62d0c3cc6dddbf3d756aa371b8b33fb5058))


### Documentation

* **skill:** drop obsolete import-workflow injection guards ([#889](https://github.com/jentic/jentic-one/issues/889)) ([2ea0591](https://github.com/jentic/jentic-one/commit/2ea0591d0138efc5890da6de1e562e9388080f3a))

## [0.24.0](https://github.com/jentic/jentic-one/compare/v0.23.0...v0.24.0) (2026-07-31)


### ⚠ BREAKING CHANGES

* **broker/auth:** self-contained JWTs presented at the broker edge must now embed an actor_type claim of "agent" or "service_account" (alongside sub and exp). External trusted (JWKS) issuers must update their minting before upgrading; a token without actor_type — previously treated as an agent — is now refused with a 401.

### Features

* **access-requests:** composite multi-item access requests end to end ([#869](https://github.com/jentic/jentic-one/issues/869)) ([33c7e23](https://github.com/jentic/jentic-one/commit/33c7e23f1910ff3e3c23a8afbfea07e549e0b55d))
* **access-requests:** surface already-satisfied items and adopt existing artifacts in fulfilment wizard ([#885](https://github.com/jentic/jentic-one/issues/885)) ([5d6ecfe](https://github.com/jentic/jentic-one/commit/5d6ecfe5f5bfc656a7649fe635f88f4391f7f3d3))
* **ui:** rebuild the agents pages as an identity console ([#878](https://github.com/jentic/jentic-one/issues/878)) ([73c632b](https://github.com/jentic/jentic-one/commit/73c632b049b6eb0032527106f84b25b0a82780d0))


### Bug Fixes

* **broker/auth:** typed token errors, fail-closed actor_type, refusal logging ([#880](https://github.com/jentic/jentic-one/issues/880)) ([44268e6](https://github.com/jentic/jentic-one/commit/44268e6adcb6f878c4896f0ae6d13c6e7ef7c592))
* **credentials:** honest updated_at, immutable api_key binding, vendor-wide reuse ([#881](https://github.com/jentic/jentic-one/issues/881)) ([76d156b](https://github.com/jentic/jentic-one/commit/76d156b7f778836d1c993af7f8fec46b7e3861d1))
* **ingest:** resolve effective operation security op-level-else-document-level ([#886](https://github.com/jentic/jentic-one/issues/886)) ([3961336](https://github.com/jentic/jentic-one/commit/3961336dac699a7775d10463133970e7b411dfc0))


### Documentation

* **skills:** add contribute-spec-fix skill (overlay fix -&gt; PR -&gt; optional local apply) ([774a56e](https://github.com/jentic/jentic-one/commit/774a56e66f7173be6b5d70624109a5603fb5a83e))
* **skills:** add import-new-api skill (new-API import flow) ([572f950](https://github.com/jentic/jentic-one/commit/572f950d23bd0d84e23de67b43a128db25301e16))

## [0.23.0](https://github.com/jentic/jentic-one/compare/v0.22.0...v0.23.0) (2026-07-29)


### Features

* **broker:** execute credential attribution + upstream passthrough fidelity ([#791](https://github.com/jentic/jentic-one/issues/791)) ([0379a0d](https://github.com/jentic/jentic-one/commit/0379a0d6a38227fdca65d3d2c499685c444db8a3))
* **control:** expose public GET /instance backend-identity endpoint ([#702](https://github.com/jentic/jentic-one/issues/702)) ([#733](https://github.com/jentic/jentic-one/issues/733)) ([0a2fed7](https://github.com/jentic/jentic-one/commit/0a2fed767a6c0804726c8710809b2409da4e84a7))
* **release:** publish app image to GHCR + document self-hosted deploy ([#732](https://github.com/jentic/jentic-one/issues/732)) ([7ac00a0](https://github.com/jentic/jentic-one/commit/7ac00a0ef197943caf5d9017229010f6df96ba30))
* theme-3 access-request residuals + broker visibility ([#778](https://github.com/jentic/jentic-one/issues/778)) ([#792](https://github.com/jentic/jentic-one/issues/792)) ([d4a6408](https://github.com/jentic/jentic-one/commit/d4a64082de1baf18d97c455d226253d9b7cd91b4))
* **toolkits:** rebuild the toolkit pages as a tabbed safety console ([1ab0e34](https://github.com/jentic/jentic-one/commit/1ab0e34a31db6344b18a65444aae3c952ab4cdf6))


### Bug Fixes

* **cli/install,broker:** reuse secrets on reinstall; map DecryptionError to 424 ([#794](https://github.com/jentic/jentic-one/issues/794)) ([3138814](https://github.com/jentic/jentic-one/commit/313881404c78a5ac3a4c36fa0f0d4ff245728f86))

## [0.22.0](https://github.com/jentic/jentic-one/compare/v0.21.0...v0.22.0) (2026-07-29)


### Features

* **access-requests:** filer-owner enrichment, widened UI type, shared queue helpers ([#858](https://github.com/jentic/jentic-one/issues/858)) ([5c55059](https://github.com/jentic/jentic-one/commit/5c550594eba0edd4fca0c5bab657d8916d1947ea))
* **ui:** rebuild dashboard into layered gateway-health overview ([#859](https://github.com/jentic/jentic-one/issues/859)) ([1c22567](https://github.com/jentic/jentic-one/commit/1c225675f316cf300857803784dd6f076bc62bc1))


### Bug Fixes

* **auth:** fail closed on missing or unknown actor_type in verify_token ([#863](https://github.com/jentic/jentic-one/issues/863)) ([ecd5c17](https://github.com/jentic/jentic-one/commit/ecd5c179b4bf6ce1e78450a69fbe393227c05dd6))
* **auth:** repair expired-token login race and add sliding web sessions ([#857](https://github.com/jentic/jentic-one/issues/857)) ([d716c15](https://github.com/jentic/jentic-one/commit/d716c1505d8e1c4478265f6cb76f630b08e44059))

## [0.21.0](https://github.com/jentic/jentic-one/compare/v0.20.0...v0.21.0) (2026-07-28)


### Features

* **cli:** delegate Homebrew-managed CLI updates to `brew upgrade` ([#855](https://github.com/jentic/jentic-one/issues/855)) ([a6cdd4e](https://github.com/jentic/jentic-one/commit/a6cdd4ea886fe2233a2c1cb73bbfbc1b71251a04))
* **cli:** refuse self-update of Homebrew-managed installs ([#854](https://github.com/jentic/jentic-one/issues/854)) ([c9375ca](https://github.com/jentic/jentic-one/commit/c9375cafe19a6bd9ea9b323733187626ce10bb79))
* **ui:** reorder toolkit hierarchy and enable two-way agent↔toolkit binding ([#797](https://github.com/jentic/jentic-one/issues/797)) ([ccd9441](https://github.com/jentic/jentic-one/commit/ccd944174d4d28f75891d2d6cdd21caee3f50896)), closes [#636](https://github.com/jentic/jentic-one/issues/636) [#637](https://github.com/jentic/jentic-one/issues/637) [#607](https://github.com/jentic/jentic-one/issues/607) [#591](https://github.com/jentic/jentic-one/issues/591)


### Bug Fixes

* **cli:** skill-install funnel — honest list, non-TTY default, ratified scopes ([#824](https://github.com/jentic/jentic-one/issues/824)) ([a4fdedb](https://github.com/jentic/jentic-one/commit/a4fdedb8872c750619bc1a5367606a17ce6f4937))
* **monitor:** show exact day-aligned windows in the Execution Volume chart ([f8963e3](https://github.com/jentic/jentic-one/commit/f8963e3735d7b192c60c1609096b6c3cdf853232))


### Documentation

* **monitor:** correct stale trend-length and NULL-key comments ([0a60457](https://github.com/jentic/jentic-one/commit/0a6045776acd64c64cc620583921d9bcf2fc5332))
* **onboarding:** disambiguate self-hosted Jentic One from the Jentic cloud platform ([#851](https://github.com/jentic/jentic-one/issues/851)) ([2a5ccfd](https://github.com/jentic/jentic-one/commit/2a5ccfdeed8ebd2e6dcc6df785e9aee46aedfd85))
* **skill:** stopped-instance branch, backend-identity check, honest rule proposals ([#843](https://github.com/jentic/jentic-one/issues/843)) ([45444f3](https://github.com/jentic/jentic-one/commit/45444f3846782731adb85335188319545b4bc59e))

## [0.20.0](https://github.com/jentic/jentic-one/compare/v0.19.0...v0.20.0) (2026-07-27)


### Features

* **ui:** live agent-registration surfaces, agent-named toolkits, generated reference docs ([#807](https://github.com/jentic/jentic-one/issues/807)) ([cb0a20a](https://github.com/jentic/jentic-one/commit/cb0a20ace99d8562b80b4b2a977af2f289a978a5))
* **ui:** port the jentic-mini Monitor Overview onto GET /monitoring/usage ([#808](https://github.com/jentic/jentic-one/issues/808)) ([ba30d1f](https://github.com/jentic/jentic-one/commit/ba30d1fce715faa5a6334f8654a459e9c70eafc0)), closes [#386](https://github.com/jentic/jentic-one/issues/386)
* **web:** serve the onboarding skill and llms.txt from the deployment ([#810](https://github.com/jentic/jentic-one/issues/810)) ([463d583](https://github.com/jentic/jentic-one/commit/463d58366a47da96ec0a0d4b06f1c4cc0585fdc1))


### Bug Fixes

* **deploy,app,tests:** make the Helm smoke matrix green and gate releases on it ([#793](https://github.com/jentic/jentic-one/issues/793)) ([72df0f3](https://github.com/jentic/jentic-one/commit/72df0f3f46c3aac4a0bbe6af1e1d607aaea780b3))
* **smoke:** skip harness tests when smoke-upstream is not deployed ([77554a4](https://github.com/jentic/jentic-one/commit/77554a4a1129ce9557d3b747622c27eaae51bd33))
* **ui:** upgrade react-router to v8 ([#811](https://github.com/jentic/jentic-one/issues/811)) ([af6fd24](https://github.com/jentic/jentic-one/commit/af6fd24bb560f0f521973dd0197d1bf38ace8210))
* **web:** sync the served onboarding skill with the CLI embed ([#822](https://github.com/jentic/jentic-one/issues/822)) ([7fb89f6](https://github.com/jentic/jentic-one/commit/7fb89f629442ee386c25d8a932a25730f1c87c60))

## [0.19.0](https://github.com/jentic/jentic-one/compare/v0.18.0...v0.19.0) (2026-07-24)


### Features

* **control:** generic access-filter seam for extension read scoping ([#769](https://github.com/jentic/jentic-one/issues/769)) ([8ca6267](https://github.com/jentic/jentic-one/commit/8ca62671b4788db039cebdd05a02723d13ba9676))

## [0.18.0](https://github.com/jentic/jentic-one/compare/v0.17.0...v0.18.0) (2026-07-24)


### Features

* **access-requests:** provisioning-plan access request ([#757](https://github.com/jentic/jentic-one/issues/757)) ([138cb42](https://github.com/jentic/jentic-one/commit/138cb4262fc5f570930ae632c7213e86bd355298))


### Bug Fixes

* **credentials:** canonical identity matching across broker/control ([#775](https://github.com/jentic/jentic-one/issues/775), [#746](https://github.com/jentic/jentic-one/issues/746), [#747](https://github.com/jentic/jentic-one/issues/747), [#748](https://github.com/jentic/jentic-one/issues/748)) ([#784](https://github.com/jentic/jentic-one/issues/784)) ([71c5a04](https://github.com/jentic/jentic-one/commit/71c5a048e1b4cfb3e2a99487cb7895f0433c749c))
* **permissions:** overhaul rule authoring, storage, and enforcement ([#655](https://github.com/jentic/jentic-one/issues/655), [#751](https://github.com/jentic/jentic-one/issues/751), [#750](https://github.com/jentic/jentic-one/issues/750), [#578](https://github.com/jentic/jentic-one/issues/578)) ([#786](https://github.com/jentic/jentic-one/issues/786)) ([2967dc2](https://github.com/jentic/jentic-one/commit/2967dc255f37074ce4a28e5d684d2e42dcc26ad8))

## [0.17.0](https://github.com/jentic/jentic-one/compare/v0.16.0...v0.17.0) (2026-07-24)


### Features

* **admin:** derive expired invite state at read time ([#782](https://github.com/jentic/jentic-one/issues/782)) ([2456eef](https://github.com/jentic/jentic-one/commit/2456eefaf94a351ee3e00f6e1dcac0bb4ba93898))
* **ui:** discovery, import entry point, and simpler delete confirm ([#767](https://github.com/jentic/jentic-one/issues/767)) ([650265c](https://github.com/jentic/jentic-one/commit/650265c67cfd34d7f84b18f7055a79e0a2d8f54c))


### Bug Fixes

* **registry:** restore operation inputs (parameters + requestBody) on import ([#773](https://github.com/jentic/jentic-one/issues/773)) ([f82b8c7](https://github.com/jentic/jentic-one/commit/f82b8c7545f779447e57b1f48376fb8a22d3b297)), closes [#768](https://github.com/jentic/jentic-one/issues/768)

## [0.16.0](https://github.com/jentic/jentic-one/compare/v0.15.3...v0.16.0) (2026-07-23)


### Features

* **auth:** invite-redemption page for finishing account creation ([#734](https://github.com/jentic/jentic-one/issues/734)) ([d758df4](https://github.com/jentic/jentic-one/commit/d758df4981baa4badd56426d49a30333dc71cffd))
* **cli:** make jenticctl update version/tag-driven and default confirm to Yes ([#766](https://github.com/jentic/jentic-one/issues/766)) ([591a327](https://github.com/jentic/jentic-one/commit/591a32716f8659505b31eda9b5d91628e923a462))

## [0.15.3](https://github.com/jentic/jentic-one/compare/v0.15.2...v0.15.3) (2026-07-22)


### Bug Fixes

* **install.sh:** re-exec under full bash from POSIX-mode /bin/sh ([#764](https://github.com/jentic/jentic-one/issues/764)) ([b98f205](https://github.com/jentic/jentic-one/commit/b98f20532b5488dabaefd9227d13fef8bf4e4d03))
* **registry:** preserve path params for RFC 6570 reserved-expansion paths (e.g. {+property}) ([#759](https://github.com/jentic/jentic-one/issues/759)) ([#762](https://github.com/jentic/jentic-one/issues/762)) ([fb03462](https://github.com/jentic/jentic-one/commit/fb034626bbade95b96a18fa7b0db255bfe83be98))

## [0.15.2](https://github.com/jentic/jentic-one/compare/v0.15.1...v0.15.2) (2026-07-22)


### Bug Fixes

* **update:** resolve v-prefixed release tags for bare-semver refs ([#760](https://github.com/jentic/jentic-one/issues/760)) ([107d530](https://github.com/jentic/jentic-one/commit/107d530614be07dfc22e5c5a537b83cdeb4d1e35))

## [0.15.1](https://github.com/jentic/jentic-one/compare/v0.15.0...v0.15.1) (2026-07-22)


### Bug Fixes

* **broker:** parse permission-rule JSON columns on the SQLite read path ([#756](https://github.com/jentic/jentic-one/issues/756)) ([74f1a5e](https://github.com/jentic/jentic-one/commit/74f1a5e4c6fab5fa7e4bfd3614776892634d18c0))
* **build:** exclude generated src/jentic_one/static from Docker context ([#729](https://github.com/jentic/jentic-one/issues/729)) ([83403a1](https://github.com/jentic/jentic-one/commit/83403a17f4d50dc15f093a9aea7b3f4545b53494)), closes [#654](https://github.com/jentic/jentic-one/issues/654)
* **control:** widen credentials.api_version and map DB data errors to 4xx ([#722](https://github.com/jentic/jentic-one/issues/722)) ([b0da8d0](https://github.com/jentic/jentic-one/commit/b0da8d0dea0be6a5388a293bf3171aea1ec92fa8)), closes [#690](https://github.com/jentic/jentic-one/issues/690)
* **install:** reliably add ~/.jentic/bin to PATH ([#730](https://github.com/jentic/jentic-one/issues/730)) ([97e0b8f](https://github.com/jentic/jentic-one/commit/97e0b8ff14cf71b0a1741cc4fc8e49bb49e5725b))
* **registry,control,broker:** stop stranded credentials colliding on API re-import ([#643](https://github.com/jentic/jentic-one/issues/643)) ([#728](https://github.com/jentic/jentic-one/issues/728)) ([16287d5](https://github.com/jentic/jentic-one/commit/16287d51b3ba00537c2e78cbd815b54ac5f3cba0))
* **ui:** use a dedicated muted token for input placeholder text ([#736](https://github.com/jentic/jentic-one/issues/736)) ([4d79812](https://github.com/jentic/jentic-one/commit/4d7981288c63826de2fdd6847e62ab1ff1335d8b)), closes [#673](https://github.com/jentic/jentic-one/issues/673)

## [0.15.0](https://github.com/jentic/jentic-one/compare/v0.14.3...v0.15.0) (2026-07-21)


### Features

* **auth:** resolve toolkit binding names in /me whoami ([#686](https://github.com/jentic/jentic-one/issues/686)) ([#726](https://github.com/jentic/jentic-one/issues/726)) ([45c4683](https://github.com/jentic/jentic-one/commit/45c4683a9084892a61137b8af8b6007a42613801))


### Bug Fixes

* **admin:** generate agent-toolkit-binding ids app-side on SQLite ([#715](https://github.com/jentic/jentic-one/issues/715)) ([d8e2006](https://github.com/jentic/jentic-one/commit/d8e20068169250b57ad11e5960d6386ca3fa3e15))
* **auth:** add token_endpoint_auth_signing_alg_values_supported to OAuth metadata ([#712](https://github.com/jentic/jentic-one/issues/712)) ([7926e6d](https://github.com/jentic/jentic-one/commit/7926e6df781db2ed696d8c400fd9f6c7d88d40a0))
* **broker:** hint at region/server-variable mismatch on upstream 401/403 ([#638](https://github.com/jentic/jentic-one/issues/638)) ([#717](https://github.com/jentic/jentic-one/issues/717)) ([9098a71](https://github.com/jentic/jentic-one/commit/9098a7113868cce74c9908ff23975883ea6d6dc7))
* **broker:** make no_toolkit_binding directive recommend credential-first order ([#720](https://github.com/jentic/jentic-one/issues/720)) ([1292b1e](https://github.com/jentic/jentic-one/commit/1292b1edf1045ee5ecc34f965697c4c266aa8282)), closes [#683](https://github.com/jentic/jentic-one/issues/683)
* **cli:** make broker default host bare to avoid double scheme ([#724](https://github.com/jentic/jentic-one/issues/724)) ([be0c945](https://github.com/jentic/jentic-one/commit/be0c945eb8add345e4280587cdf379d6ef5ef984)), closes [#657](https://github.com/jentic/jentic-one/issues/657)
* **control:** let a bound agent read its toolkit and its credentials ([#665](https://github.com/jentic/jentic-one/issues/665), [#682](https://github.com/jentic/jentic-one/issues/682)) ([#718](https://github.com/jentic/jentic-one/issues/718)) ([5f68945](https://github.com/jentic/jentic-one/commit/5f689456f1a5d2dfa339a0e6863f94ce3ff5f14f))
* **control:** let a bound agent write to its toolkit and 403 (not 404) when scope-hidden ([#725](https://github.com/jentic/jentic-one/issues/725)) ([16bdbdb](https://github.com/jentic/jentic-one/commit/16bdbdb0add21a02687c02b4be80b66c6af023fd)), closes [#682](https://github.com/jentic/jentic-one/issues/682)
* **control:** normalize credential api_vendor/api_name to registry slug ([#719](https://github.com/jentic/jentic-one/issues/719)) ([083d871](https://github.com/jentic/jentic-one/commit/083d87127c5096e85a469599283df07a42612023)), closes [#656](https://github.com/jentic/jentic-one/issues/656)
* **registry:** make spec re-import idempotent and surface readable errors ([#721](https://github.com/jentic/jentic-one/issues/721)) ([2b93cfd](https://github.com/jentic/jentic-one/commit/2b93cfd0561deec99eb5819001e51683899c1ea7)), closes [#688](https://github.com/jentic/jentic-one/issues/688)
* **registry:** reload API view after promote-over-live to avoid MissingGreenlet ([#723](https://github.com/jentic/jentic-one/issues/723)) ([0eb426d](https://github.com/jentic/jentic-one/commit/0eb426d6fb556733ba7c1d8f7629aa080cd491d5)), closes [#642](https://github.com/jentic/jentic-one/issues/642)


### Documentation

* **control,broker:** clarify permission rules and broker path format ([#576](https://github.com/jentic/jentic-one/issues/576)) ([a00a974](https://github.com/jentic/jentic-one/commit/a00a974ff4a1540a0813c0b8c28aa1dbe4ac132b))
* **intake:** point de-dup at the candidate_issues list, not a live search ([#649](https://github.com/jentic/jentic-one/issues/649)) ([9269ba6](https://github.com/jentic/jentic-one/commit/9269ba6671e85025a546bcdc8f13e0437f55d230))

## [0.14.3](https://github.com/jentic/jentic-one/compare/v0.14.2...v0.14.3) (2026-07-20)


### CI/CD

* **release:** force patch release to republish v0.14.2 artifacts ([#710](https://github.com/jentic/jentic-one/issues/710)) ([af65126](https://github.com/jentic/jentic-one/commit/af6512617e9e5000e6921b0b4c38c10f546f43ad))

## [0.14.2](https://github.com/jentic/jentic-one/compare/v0.14.1...v0.14.2) (2026-07-20)


### Bug Fixes

* **access-requests:** replace leaked &lt;missing&gt; placeholder with actionable field error ([#565](https://github.com/jentic/jentic-one/issues/565)) ([674ce8a](https://github.com/jentic/jentic-one/commit/674ce8af7b81bbd7726ba44f166e3db97cafa28e))

## [0.14.1](https://github.com/jentic/jentic-one/compare/v0.14.0...v0.14.1) (2026-07-20)


### Bug Fixes

* **auth:** prevent SQLite deadlock in JWT assertion token exchange ([#580](https://github.com/jentic/jentic-one/issues/580)) ([44a577d](https://github.com/jentic/jentic-one/commit/44a577d44044a94f77aca4f0692c0aabba864ffd))
* **auth:** set owner_id on DCR agent approval for toolkit visibility ([#563](https://github.com/jentic/jentic-one/issues/563)) ([b6f0025](https://github.com/jentic/jentic-one/commit/b6f0025a581eccb5f087282adc529d9cfca99853))

## [0.14.0](https://github.com/jentic/jentic-one/compare/v0.13.2...v0.14.0) (2026-07-20)


### Features

* **ci:** add ux and ax experience labels to intake taxonomy ([#590](https://github.com/jentic/jentic-one/issues/590)) ([2061eb1](https://github.com/jentic/jentic-one/commit/2061eb1ecde99d8803f6b62423aa533c07c065ac))
* **cli:** export tree builders + core.Run for downstream CLI composition ([#661](https://github.com/jentic/jentic-one/issues/661)) ([8563dec](https://github.com/jentic/jentic-one/commit/8563dec796d312bf1a4bc6492a493a9c5c729f77))
* **credentials:** Tier-1 credentials revamp, health, audit & toolkit surfaces ([#499](https://github.com/jentic/jentic-one/issues/499)) ([918c9dc](https://github.com/jentic/jentic-one/commit/918c9dc93340b89c8c655083988846dcba45649f))
* **oss:** migrate david contributions ([40627bc](https://github.com/jentic/jentic-one/commit/40627bcf8f96af140b14cef0cf2de07d46599cf9))
* **oss:** migrate manuel jentic contributions ([db3cb26](https://github.com/jentic/jentic-one/commit/db3cb26a1a7a8e05e44ede381f963376dbd8b83c))
* **oss:** migrate renton mcneill contributions ([2654c92](https://github.com/jentic/jentic-one/commit/2654c92ccc5ddc802e550e607e03c121521548c2))
* **scopes:** add catalog:import scope, default-on for agents ([6b53c7d](https://github.com/jentic/jentic-one/commit/6b53c7d2b4e855ac31c2f9d70b3d75134b39cab6))
* **scopes:** add catalog:import scope, default-on for agents ([1b263c1](https://github.com/jentic/jentic-one/commit/1b263c17feec9b513c959e8c14e091f687261d3b))
* **ui:** add extraRoutes seam to App for downstream SPA composition ([#664](https://github.com/jentic/jentic-one/issues/664)) ([61e720a](https://github.com/jentic/jentic-one/commit/61e720aa144f8e850c3c927da4bf27af6fd2ea8f))
* **ui:** align fonts, design tokens, navigation, and page shell with jentic-webapp ([#408](https://github.com/jentic/jentic-one/issues/408)) ([5f88bc4](https://github.com/jentic/jentic-one/commit/5f88bc4a3925269f85cfc85ab50c8c52264d5120))
* **ui:** Monitor page with cross-linked traces/jobs ([#477](https://github.com/jentic/jentic-one/issues/477)) ([558bd7b](https://github.com/jentic/jentic-one/commit/558bd7bf7b386fd1636397c8898a0be343261d7b)), closes [#457](https://github.com/jentic/jentic-one/issues/457)
* **workspace+discover:** unified Discover surface and Workspace management ([#447](https://github.com/jentic/jentic-one/issues/447)) ([619a294](https://github.com/jentic/jentic-one/commit/619a294f683375d6e667b6f2af4c6d6b8fcb07d3))


### Bug Fixes

* **auth:** retry DCR admin-DB write on transient SQLite lock ([#548](https://github.com/jentic/jentic-one/issues/548)) ([066d2c4](https://github.com/jentic/jentic-one/commit/066d2c4a4c6fc2d213b4806f6736517b12ce2560))
* **broker:** drop PBAC and identity caches from 30s to 3s to reduce staleness window ([#545](https://github.com/jentic/jentic-one/issues/545)) ([3cd1bd7](https://github.com/jentic/jentic-one/commit/3cd1bd784adbbc5770e8abccfdeb96da7248c30a))
* **ci:** shorten ax label description to under 100 chars ([#598](https://github.com/jentic/jentic-one/issues/598)) ([db2c87a](https://github.com/jentic/jentic-one/commit/db2c87a0f7f5d017af77d3661422d8b2a602076f))
* **ci:** workflow missing dep ([99f9d60](https://github.com/jentic/jentic-one/commit/99f9d6001662e53ef048ddee63084dbed0b6f4ee))
* **cli:** fail fast when docker daemon is unreachable ([85ee0db](https://github.com/jentic/jentic-one/commit/85ee0db8650f3fc3b3a348027845faec89fa697e))
* **cli:** resolve uv venv and ui build issues for local installs ([92fbbc2](https://github.com/jentic/jentic-one/commit/92fbbc2f66d2a744ccf69c066485457874e31ac0))
* **cli:** resolve uv venv and ui build issues for local installs ([7e3beae](https://github.com/jentic/jentic-one/commit/7e3beae5b2f410f31ca73c82ab5c3ad2b2f30f15)), closes [#535](https://github.com/jentic/jentic-one/issues/535)
* **cli:** stop telemetry consent prompt swallowing the first Enter ([#546](https://github.com/jentic/jentic-one/issues/546)) ([a113237](https://github.com/jentic/jentic-one/commit/a113237f34a37cbd3b8fc316e816f9f45d2bb821))
* **db:** eliminate SQLite "database is locked" via BEGIN IMMEDIATE ([f2d2fb1](https://github.com/jentic/jentic-one/commit/f2d2fb134bd1c287e148f9b2b943863df548ca27))
* **github:** intake output-guard + Slack notification polish ([#582](https://github.com/jentic/jentic-one/issues/582)) ([6131e3e](https://github.com/jentic/jentic-one/commit/6131e3e811512972ace671fc4fb4d49fc780e052))
* **install:** sync build source by fetch+reset so a rewritten main can't dead-end install ([b84bca4](https://github.com/jentic/jentic-one/commit/b84bca49add36a3a7fb9449588226833e3552756))
* **install:** sync build source by fetch+reset so a rewritten main can't dead-end install ([7b28f93](https://github.com/jentic/jentic-one/commit/7b28f9317eb0f563ff76299bc497968b96b16327))
* **readme:** remove bad link ([0749ba3](https://github.com/jentic/jentic-one/commit/0749ba362065f9c7ff0940f509f170b787f7b211))
* **search:** include active IMPORTED revisions in lexical search ([30bb463](https://github.com/jentic/jentic-one/commit/30bb46309e4f4be5527c55a2fb944b9cdef116f7))
* **search:** include active IMPORTED revisions in lexical search ([78c09b9](https://github.com/jentic/jentic-one/commit/78c09b9131be7930bc53dd3488c7c30edea38a3f))
* **search:** render FTS config as regconfig so Postgres lexical search works ([172f76e](https://github.com/jentic/jentic-one/commit/172f76ef4245a7760f35b19da24204314d35286d))
* **search:** render the FTS config as regconfig so Postgres lexical search works ([e555e02](https://github.com/jentic/jentic-one/commit/e555e022618effdffe652b5e75733fc2321fc156))
* **security:** resolve token scopes live from actor grants ([57b5a59](https://github.com/jentic/jentic-one/commit/57b5a59c94cd51c32b72e4f2aae840ee643659ed))
* **security:** resolve token scopes live from actor grants ([f2d5283](https://github.com/jentic/jentic-one/commit/f2d5283874331694b181ee95a95147ebfad15d8e)), closes [#531](https://github.com/jentic/jentic-one/issues/531)
* **sqlite:** eliminate "database is locked" via write-scoped BEGIN IMMEDIATE ([6c8d556](https://github.com/jentic/jentic-one/commit/6c8d55613c887c3e19fe8ec0deadfe0d124bc767))
* **sqlite:** scope BEGIN IMMEDIATE to writes, not reads ([c648e13](https://github.com/jentic/jentic-one/commit/c648e13f1faf224310e0b93866900579c177d8ef))
* **test_postgres_lexical.py:** silence mypy no-untyped-call on stmt.compile ([9a80e1f](https://github.com/jentic/jentic-one/commit/9a80e1fc3e36760f364f588cd1ccb538dd08fe07))
* **uninstall:** remove docker data volume by name on purge ([#547](https://github.com/jentic/jentic-one/issues/547)) ([bc06be0](https://github.com/jentic/jentic-one/commit/bc06be05e2144511dbda159f6af3fa875236dec4))
* update trivy-action version ([fa81d98](https://github.com/jentic/jentic-one/commit/fa81d985256fb2f93542da81fb3e02f07178eb5b))
* use master branch for trivy action ([c9a1ff0](https://github.com/jentic/jentic-one/commit/c9a1ff02eb7efc7089b1ccf9c8fc2a8a162d4371))


### Refactors

* **auth:** encode token lifecycle via is_ephemeral column ([7efb160](https://github.com/jentic/jentic-one/commit/7efb160ad7222cb5c9e4f298bc57f3b411a5f5df))
* **compose.go:** use postgres:16 instead of pgvector image ([#549](https://github.com/jentic/jentic-one/issues/549)) ([97826eb](https://github.com/jentic/jentic-one/commit/97826ebd79251f19ac2bc5f71fa619ba8045d3bc))
* **install:** satisfy gosec on UI build/copy helpers ([79f4bf5](https://github.com/jentic/jentic-one/commit/79f4bf5361259443465f4d59e72ca8def4fb94ee))
* **oss:** migrate to opensourceable codebase ([77c923f](https://github.com/jentic/jentic-one/commit/77c923f4aba658335bb63fdec926d5ba9bb91391))
* **seams:** add pluggable extension points across backend, CLI, and UI ([#562](https://github.com/jentic/jentic-one/issues/562)) ([61d67e6](https://github.com/jentic/jentic-one/commit/61d67e6a6188be8b63b815fd5c861c4319214cd7))
* **token_resolver.py:** use SQLAlchemy Boolean type for is_ephemeral ([ce1a8ff](https://github.com/jentic/jentic-one/commit/ce1a8ff345ae8a647f6cb9f3579ad00f4da3019e))


### Documentation

* add public beta warning and quick start to README ([c98d162](https://github.com/jentic/jentic-one/commit/c98d16225b847d93cc31998ba87508ae4ccf53a3))
* add public beta warning banner to README ([dd00a81](https://github.com/jentic/jentic-one/commit/dd00a81062861efd678710bc26fe2b51e64793b7))
* explicitly name jenticctl in quick start ([9072f32](https://github.com/jentic/jentic-one/commit/9072f3244e2531d7106164b13548809d303d454c))
* hoist quick start install command to top of README ([383add4](https://github.com/jentic/jentic-one/commit/383add414e9454491043f1b92af1ba713d4e998d))
* **skill:** reflect catalog:import default-grant for cataloged imports ([#550](https://github.com/jentic/jentic-one/issues/550)) ([4098999](https://github.com/jentic/jentic-one/commit/409899916880aa8e5721b75c776f0f22d2024434))


### Build System

* **release:** implement the beta-blocking release automation (release-please + GoReleaser) ([#667](https://github.com/jentic/jentic-one/issues/667)) ([39b20c1](https://github.com/jentic/jentic-one/commit/39b20c1b5551b1d7bd6725f519b95a222b273f51))
