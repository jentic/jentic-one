# Changelog

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
