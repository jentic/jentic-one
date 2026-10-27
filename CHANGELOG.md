# Changelog

## [0.44.0](https://github.com/jentic/jentic-one/compare/v0.43.0...v0.44.0) (2026-10-27)


### ⚠ BREAKING CHANGES

* **auth:** agents follow their owner's account status ([#1866](https://github.com/jentic/jentic-one/issues/1866))
* **admin:** GET /monitoring/usage and GET /executions no longer accept a toolkit_id filter. A request that still sends ?toolkit_id= is not rejected; the parameter is ignored, the unfiltered result is returned, and a removed_query_param_ignored INFO line is logged.

### Features

* **admin:** add MonitoringService.get_grouped_totals seam ([#1667](https://github.com/jentic/jentic-one/issues/1667)) ([82da0ce](https://github.com/jentic/jentic-one/commit/82da0cedd9e5c8287472d36e639b1c305ce9e068))
* **admin:** re-verify permissions on open event streams ([#1836](https://github.com/jentic/jentic-one/issues/1836)) ([#1911](https://github.com/jentic/jentic-one/issues/1911)) ([2c31451](https://github.com/jentic/jentic-one/commit/2c31451320d6c7b45e52ffea63132b0addbe51ae))
* **admin:** run registered hooks after a user is deleted ([#1862](https://github.com/jentic/jentic-one/issues/1862)) ([435173d](https://github.com/jentic/jentic-one/commit/435173d02f20fff0e382d15174acb0b8ec963023))
* **auth:** allow http jwks_url for trusted issuers outside production ([#1757](https://github.com/jentic/jentic-one/issues/1757)) ([acd7168](https://github.com/jentic/jentic-one/commit/acd71688785bcfa366e6499d8d9df591a22d3438)), closes [#1723](https://github.com/jentic/jentic-one/issues/1723)
* **broker:** give the broker-side LocalProtocolError fallback its own problem type ([#1918](https://github.com/jentic/jentic-one/issues/1918)) ([0d0e26d](https://github.com/jentic/jentic-one/commit/0d0e26ddf9fe731f831d30866618b9da6dabce23))
* **broker:** invalidate binding, rule, and token caches on unbind, disable, and revoke ([#1928](https://github.com/jentic/jentic-one/issues/1928)) ([cf7b79d](https://github.com/jentic/jentic-one/commit/cf7b79d12304e8925d1a1b007975721cc2b5dafe)), closes [#1837](https://github.com/jentic/jentic-one/issues/1837)
* **broker:** require-approval (ask tier) — hold executions for human approval ([#1582](https://github.com/jentic/jentic-one/issues/1582)) ([253f462](https://github.com/jentic/jentic-one/commit/253f462a04cb2310ba275c7f0933f1f45849b8df))
* **catalog:** single-flight the update sweep across processes ([#1860](https://github.com/jentic/jentic-one/issues/1860)) ([dce82cd](https://github.com/jentic/jentic-one/commit/dce82cd6e2626a672a1f7d87f33939b6d8405440)), closes [#1844](https://github.com/jentic/jentic-one/issues/1844)
* **cli:** clean up installed agent skills on jenticctl uninstall ([#1772](https://github.com/jentic/jentic-one/issues/1772)) ([426fafa](https://github.com/jentic/jentic-one/commit/426fafaad2f47c49a15a5738bc46e41811d78eaf))
* **cli:** harden multipart execute (dry-run parts, size guard, part overrides) ([#1683](https://github.com/jentic/jentic-one/issues/1683)) ([d32125e](https://github.com/jentic/jentic-one/commit/d32125ea99169199f80a4128dae59e0292d6a1e6))
* **cli:** hint at jenticctl start when execute hits a refused loopback connection ([#1650](https://github.com/jentic/jentic-one/issues/1650)) ([3e8433b](https://github.com/jentic/jentic-one/commit/3e8433b692e2d18d5175203bad42acf993490f49))
* **cli:** skill init/update/remove --json outcomes and skill staleness signal ([#1670](https://github.com/jentic/jentic-one/issues/1670)) ([7cffb5e](https://github.com/jentic/jentic-one/commit/7cffb5e6332d951ad6fd7c43153bd00f52e2db76))
* **control:** include binding suspended_reason in credential agents listing ([#1649](https://github.com/jentic/jentic-one/issues/1649)) ([62f0767](https://github.com/jentic/jentic-one/commit/62f0767f114a609bfef78761d0b901ebf49e7640))
* **control:** scope permission rule set binding_count to visible bindings ([#1915](https://github.com/jentic/jentic-one/issues/1915)) ([e49b650](https://github.com/jentic/jentic-one/commit/e49b65028f13dcb4259b014ebd6dbf6735bc53e4))
* **jobs:** cancel an actor's queued execution jobs on disable/archive ([#1680](https://github.com/jentic/jentic-one/issues/1680)) ([04913c8](https://github.com/jentic/jentic-one/commit/04913c85b36e9638fa8ae10ccb92eec80d764a5e))
* **registry:** let a user read notes written by agents they own ([#1912](https://github.com/jentic/jentic-one/issues/1912)) ([67970af](https://github.com/jentic/jentic-one/commit/67970af652ff45a879d6f2a456ea89d1402f578d))
* **registry:** public search-strategy composition seam and shared filter contract ([#1678](https://github.com/jentic/jentic-one/issues/1678)) ([26d0f9d](https://github.com/jentic/jentic-one/commit/26d0f9d6f45b9d2366e38ef4629aa4c3c538853e))
* **state:** make the shared-state backend pluggable ([#1871](https://github.com/jentic/jentic-one/issues/1871)) ([4a3f226](https://github.com/jentic/jentic-one/commit/4a3f22637e55aa1ffdbb392e79c8deb76b186123))
* **ui:** barrel-export monitor usage-window plumbing and reusable component tiers ([#1665](https://github.com/jentic/jentic-one/issues/1665)) ([e348798](https://github.com/jentic/jentic-one/commit/e3487986bafc7ffe09918ebcf72917e1e15e31c8))
* **web:** mark authenticated JSON responses private, no-store ([#1835](https://github.com/jentic/jentic-one/issues/1835)) ([#1910](https://github.com/jentic/jentic-one/issues/1910)) ([142959f](https://github.com/jentic/jentic-one/commit/142959fbb7f48d48d280d706e9fff8feefaa2308))


### Bug Fixes

* **admin:** accept OAuth access tokens and serve endpoints.json on standalone surfaces ([#1775](https://github.com/jentic/jentic-one/issues/1775)) ([271109e](https://github.com/jentic/jentic-one/commit/271109ec837d3a12526600b515a1542051a9936c))
* **admin:** authorize every PATCH /users/{id} change, not only email ([#1660](https://github.com/jentic/jentic-one/issues/1660)) ([c4f2fb9](https://github.com/jentic/jentic-one/commit/c4f2fb95ab99fb66152a3e94a704a8b505d0e557)), closes [#1654](https://github.com/jentic/jentic-one/issues/1654)
* **admin:** clear broker caches when a user is disabled, deleted or re-enabled ([#1981](https://github.com/jentic/jentic-one/issues/1981)) ([6f7ecae](https://github.com/jentic/jentic-one/commit/6f7ecae0ce1abfae9c2c83eea2e70a1d5ede1377))
* **admin:** filter users by derived expired/pending invite state in SQL ([#1638](https://github.com/jentic/jentic-one/issues/1638)) ([8926b10](https://github.com/jentic/jentic-one/commit/8926b1071d8846c442050402b00dfa27ea711405))
* **admin:** re-sample the clock inside the execution-stats cache lock ([#1738](https://github.com/jentic/jentic-one/issues/1738)) ([097ee6e](https://github.com/jentic/jentic-one/commit/097ee6e4b2f45c92de3f01f3e9033949dbf3ee4d))
* **admin:** report real pending and active_now in monitoring usage stats ([#1651](https://github.com/jentic/jentic-one/issues/1651)) ([cf045e5](https://github.com/jentic/jentic-one/commit/cf045e576c1cde6547eb3741d785adb4a002cf34))
* **admin:** revoke pending invites on user disable or delete ([#1931](https://github.com/jentic/jentic-one/issues/1931)) ([062c933](https://github.com/jentic/jentic-one/commit/062c9332473be95799f9488da901dad927351d3e))
* **admin:** scope actor lookup to visible actors ([#1865](https://github.com/jentic/jentic-one/issues/1865)) ([b574fb1](https://github.com/jentic/jentic-one/commit/b574fb14f162839ea646e6797ffd3d0ed84106d2))
* **admin:** stop /auth/login revealing locked accounts ([#1659](https://github.com/jentic/jentic-one/issues/1659)) ([c731fc3](https://github.com/jentic/jentic-one/commit/c731fc3cf2518ffa0080362806bc57dbbda461a1))
* **admin:** use FOR NO KEY UPDATE in last-admin row lock to avoid reissue deadlock ([#1941](https://github.com/jentic/jentic-one/issues/1941)) ([8920482](https://github.com/jentic/jentic-one/commit/8920482f0b4a6a53cafa567e177a97250c462aa7)), closes [#1802](https://github.com/jentic/jentic-one/issues/1802)
* **auth,control:** check owner deleted_at on agent liveness and unify connect rate limiter ([#1936](https://github.com/jentic/jentic-one/issues/1936)) ([7124718](https://github.com/jentic/jentic-one/commit/7124718e9a1f79453b609861228a39dc2ce24fd0))
* **auth:** agents follow their owner's account status ([#1866](https://github.com/jentic/jentic-one/issues/1866)) ([f444668](https://github.com/jentic/jentic-one/commit/f4446688fa8fac16aea68769358cf6513ed5aad3))
* **auth:** audit permission-grant wipe on agent archive ([#1640](https://github.com/jentic/jentic-one/issues/1640)) ([7d52728](https://github.com/jentic/jentic-one/commit/7d52728ce316eb87b33c3a621354da17f25d9175))
* **auth:** cap each client's share of login CSRF nonces ([#1872](https://github.com/jentic/jentic-one/issues/1872)) ([b67e286](https://github.com/jentic/jentic-one/commit/b67e28670fc8d1128ba679d32f3260883aa3354b))
* **auth:** check agent ownership before status on API key writes ([#1930](https://github.com/jentic/jentic-one/issues/1930)) ([f182775](https://github.com/jentic/jentic-one/commit/f18277564299cfae6720271fa649f183afe674f3))
* **auth:** key the authorization rate limit on client network only ([#1879](https://github.com/jentic/jentic-one/issues/1879)) ([7ea6956](https://github.com/jentic/jentic-one/commit/7ea695670880638669b2bcbc332312e95dfde0b5))
* **auth:** match trusted_proxies entries as networks ([#1881](https://github.com/jentic/jentic-one/issues/1881)) ([b31db4b](https://github.com/jentic/jentic-one/commit/b31db4b122bb2e14cc6e658caae932695a4abead))
* **auth:** re-check users.active on every login-JWT verification ([#1689](https://github.com/jentic/jentic-one/issues/1689)) ([a7fb867](https://github.com/jentic/jentic-one/commit/a7fb867223d6288c0b4f04e80f3c8649da2e569f))
* **auth:** read agents on the effective permission set ([#1952](https://github.com/jentic/jentic-one/issues/1952)) ([c465992](https://github.com/jentic/jentic-one/commit/c465992f766ce968e046410582888c6d5576d6f6))
* **auth:** report has_api_key on agent list and mutation responses ([#1642](https://github.com/jentic/jentic-one/issues/1642)) ([a02e8cc](https://github.com/jentic/jentic-one/commit/a02e8cc9d37f37e13eb1a01d480906e0da09ee59))
* **auth:** suspend non-owner credential bindings on agent ownership transfer ([#1933](https://github.com/jentic/jentic-one/issues/1933)) ([0a0e162](https://github.com/jentic/jentic-one/commit/0a0e1621a3683b4e638a032080d51f159194eba4))
* **auth:** verify upstream OIDC id_token, add PKCE, and key external identities by issuer ([#1932](https://github.com/jentic/jentic-one/issues/1932)) ([558dc16](https://github.com/jentic/jentic-one/commit/558dc16105b817cb5e464a24b17106cf14f342d3))
* **broker:** accept the public revision_id as a Jentic-Revision pin ([#1770](https://github.com/jentic/jentic-one/issues/1770)) ([284accc](https://github.com/jentic/jentic-one/commit/284accc167ffd93fc9f7e1e858c9dfc9b82ff0d1))
* **broker:** coalesce concurrent token-validation misses with SingleFlight ([#1739](https://github.com/jentic/jentic-one/issues/1739)) ([d9ee6b1](https://github.com/jentic/jentic-one/commit/d9ee6b1a28753b93755cd20d4be58fe7311cd348)), closes [#1705](https://github.com/jentic/jentic-one/issues/1705)
* **broker:** correlate denial events with the request trace and name what was denied ([#1903](https://github.com/jentic/jentic-one/issues/1903)) ([3cfe1cf](https://github.com/jentic/jentic-one/commit/3cfe1cf70ee049ed463b386f62007e0fc74671d3))
* **broker:** emit one execution lifecycle event on the async path ([#1917](https://github.com/jentic/jentic-one/issues/1917)) ([bc8a62e](https://github.com/jentic/jentic-one/commit/bc8a62ebed9290cef1c02a14924c5c066ccbd28a))
* **broker:** handle mid-stream upstream failures and always persist the streaming execution ([#1752](https://github.com/jentic/jentic-one/issues/1752)) ([52a12dd](https://github.com/jentic/jentic-one/commit/52a12ddc5ca9dcf839ebca8c302b34b9483cf0a7))
* **broker:** let RetryRunner see that a request carried an Idempotency-Key ([#1747](https://github.com/jentic/jentic-one/issues/1747)) ([5f2f2c3](https://github.com/jentic/jentic-one/commit/5f2f2c3c840e3b44ad3bc551bfa7e0ae3ce46d3a)), closes [#1711](https://github.com/jentic/jentic-one/issues/1711)
* **broker:** mount /metrics ahead of the forward-proxy catch-all ([#1729](https://github.com/jentic/jentic-one/issues/1729)) ([37091e2](https://github.com/jentic/jentic-one/commit/37091e2614f0c1978119f015a37fc89ad7c15068))
* **broker:** order credential candidates so the ambiguity 409 is deterministic ([#1902](https://github.com/jentic/jentic-one/issues/1902)) ([c3bcc46](https://github.com/jentic/jentic-one/commit/c3bcc46c52082b041e0abab404bb1b09e6cf2b26))
* **broker:** refresh OAuth tokens from the locked row and apply expiry skew once ([#1682](https://github.com/jentic/jentic-one/issues/1682)) ([0baafaf](https://github.com/jentic/jentic-one/commit/0baafaff7baacd14f0953b63a7201ccde247e010))
* **broker:** return 502 for upstream transport errors on the buffered path ([#1742](https://github.com/jentic/jentic-one/issues/1742)) ([4321d6f](https://github.com/jentic/jentic-one/commit/4321d6fa97a8556602c49e8a22ed89ca5e41b954))
* **broker:** stop holding an admin_db connection across the upstream call ([#1746](https://github.com/jentic/jentic-one/issues/1746)) ([60e25b3](https://github.com/jentic/jentic-one/commit/60e25b30990108f4993fa8e0df23828f64c4557c))
* **broker:** stop retrying keyed POST and PATCH requests after send ([#1916](https://github.com/jentic/jentic-one/issues/1916)) ([a347cb9](https://github.com/jentic/jentic-one/commit/a347cb9a3e09d39320bb917f274d2c2e1684d50f))
* **broker:** treat httpx PoolTimeout as pre-send and exclude from breaker ([#1795](https://github.com/jentic/jentic-one/issues/1795)) ([#1919](https://github.com/jentic/jentic-one/issues/1919)) ([d998457](https://github.com/jentic/jentic-one/commit/d99845738ba5a60bff44f9545a3822c26726aecf))
* **cli:** apply machine-mode gates to any non-human --mode ([#1635](https://github.com/jentic/jentic-one/issues/1635)) ([cfe700c](https://github.com/jentic/jentic-one/commit/cfe700c031cffabd015191f501bdd7a22a02c6d0))
* **cli:** emit JSON envelope for apis/catalog/endpoints in agent mode ([#1637](https://github.com/jentic/jentic-one/issues/1637)) ([5d275f2](https://github.com/jentic/jentic-one/commit/5d275f2239956c1b1395a50e5a74f907b74f0849))
* **cli:** fall back to bundled skill when hosted router points past this binary ([#1677](https://github.com/jentic/jentic-one/issues/1677)) ([6756cb1](https://github.com/jentic/jentic-one/commit/6756cb142d3eff25fff820a84f5a470278330ce7))
* **cli:** keep jenticctl update's CLI and stack halves from diverging silently ([#1681](https://github.com/jentic/jentic-one/issues/1681)) ([39e9765](https://github.com/jentic/jentic-one/commit/39e97650deb8fb29671bfbfa042df55b6a5eb597))
* **cli:** make prompts cancellable on TERM=dumb and let a second Ctrl-C kill the CLI ([#1679](https://github.com/jentic/jentic-one/issues/1679)) ([2456a87](https://github.com/jentic/jentic-one/commit/2456a87896263ed5f504ced5910bcef7baa03434))
* **cli:** seed provider config without cached login tokens ([#1909](https://github.com/jentic/jentic-one/issues/1909)) ([062e433](https://github.com/jentic/jentic-one/commit/062e4332185c52cbd9048bdaacbc7398b5ed8c3d))
* **cli:** stop context create's saved-mode flag from shadowing the session --mode ([#1636](https://github.com/jentic/jentic-one/issues/1636)) ([a3eaa0a](https://github.com/jentic/jentic-one/commit/a3eaa0a8966745352d559a23ca6784536dc63978))
* **cli:** strip omitempty/omitzero on config fields with non-zero defaults ([#1924](https://github.com/jentic/jentic-one/issues/1924)) ([2407626](https://github.com/jentic/jentic-one/commit/2407626631750217a369103b8bc0aa136b4c51b0))
* **cli:** treat broker-origin 5xx/429 as TRANSPORT_ERROR on execute ([#1672](https://github.com/jentic/jentic-one/issues/1672)) ([5faac73](https://github.com/jentic/jentic-one/commit/5faac7373906b7f7973844cbc85df728bf6141c7))
* **config:** key the one-shot config cache on ctime as well as pipe inode ([#1647](https://github.com/jentic/jentic-one/issues/1647)) ([ddfb2da](https://github.com/jentic/jentic-one/commit/ddfb2dae1aa55d27c56649c44de3951a152452a5))
* **control:** apply credential edits for OAuth2, no_auth and empty secrets ([#1657](https://github.com/jentic/jentic-one/issues/1657)) ([a776fe5](https://github.com/jentic/jentic-one/commit/a776fe51e99cf9ec77ac26e4a14fd95c5d37082e))
* **control:** bind the OAuth connect callback to the browser that started it ([#1868](https://github.com/jentic/jentic-one/issues/1868)) ([9dddd4d](https://github.com/jentic/jentic-one/commit/9dddd4d0a5f0d1bf27049242786617356a1f3142))
* **control:** honour egress allowlist on OAuth2 credential create and token refresh ([#1743](https://github.com/jentic/jentic-one/issues/1743)) ([43affca](https://github.com/jentic/jentic-one/commit/43affcadabefe7cb2528d5085552319e1483812d))
* **control:** owner-scope credential connect flow ([#1632](https://github.com/jentic/jentic-one/issues/1632)) ([2d2a4ca](https://github.com/jentic/jentic-one/commit/2d2a4cad5e66aadf71de31ab5ec6cc6c0a1e0b1e))
* **control:** purge agent credential bindings when deleting a credential ([#1927](https://github.com/jentic/jentic-one/issues/1927)) ([36c03d3](https://github.com/jentic/jentic-one/commit/36c03d3b2b436c0d51fdeeda96c709e20ab1605d)), closes [#1426](https://github.com/jentic/jentic-one/issues/1426)
* **control:** record the acting caller on connect-session cancel and respect suspended bindings ([#1867](https://github.com/jentic/jentic-one/issues/1867)) ([de252f4](https://github.com/jentic/jentic-one/commit/de252f42d43ef8db779671549190167181474db4))
* **control:** refuse connect sessions for an agent the caller does not own ([#1653](https://github.com/jentic/jentic-one/issues/1653)) ([121883b](https://github.com/jentic/jentic-one/commit/121883bba3840ae1694f98e10bfcd4672a7d0d46))
* **control:** require https for OAuth2 credential token_url and authorize_url ([#1945](https://github.com/jentic/jentic-one/issues/1945)) ([7f4bf7e](https://github.com/jentic/jentic-one/commit/7f4bf7e75a4e0cc5e02fa09d70696de29bc492ab)), closes [#1779](https://github.com/jentic/jentic-one/issues/1779)
* **db:** survive idle-VM connection loss with pool_recycle, TCP keepalive and a quiet worker reconnect ([#1673](https://github.com/jentic/jentic-one/issues/1673)) ([d376fa4](https://github.com/jentic/jentic-one/commit/d376fa45ed03489e03de29a269cedc3e4d5c8119))
* **deploy:** install the uv.lock set in container images ([#1824](https://github.com/jentic/jentic-one/issues/1824)) ([4f023b5](https://github.com/jentic/jentic-one/commit/4f023b52e5b5496e0896c4e2af9700283a6465b8))
* **deploy:** route every control surface prefix through the split gateway ([#1643](https://github.com/jentic/jentic-one/issues/1643)) ([e2311b1](https://github.com/jentic/jentic-one/commit/e2311b1f263b1968bd51cd7d3a0cf8a8f6d53fca))
* **deploy:** route the admin SPA through the split gateway; make the gateway route gate hermetic ([#1690](https://github.com/jentic/jentic-one/issues/1690)) ([b960d47](https://github.com/jentic/jentic-one/commit/b960d47c09404031630faa8f255cb43bd5cdb213))
* **events:** name the agent and the call in approval event summaries ([#1907](https://github.com/jentic/jentic-one/issues/1907)) ([bab770c](https://github.com/jentic/jentic-one/commit/bab770c87a87a9ea7f924dedd7294401a3358cf7))
* **jobs:** never requeue or re-dispatch an execution after its handler returns ([#1920](https://github.com/jentic/jentic-one/issues/1920)) ([3f1e101](https://github.com/jentic/jentic-one/commit/3f1e101d944a707b39cb6b117d679156f856f6d9))
* **jobs:** re-check actor status right before a queued execution injects credentials ([#1671](https://github.com/jentic/jentic-one/issues/1671)) ([467cd63](https://github.com/jentic/jentic-one/commit/467cd6362472cf038d45b80118d388c94a3f3cd7))
* **monitoring:** bound the overview window, narrow a slashless api_id, and read each response from one snapshot ([#1999](https://github.com/jentic/jentic-one/issues/1999)) ([8450fc0](https://github.com/jentic/jentic-one/commit/8450fc017f6abda12f67ed88fc7f2b4dd386f23f))
* **registry:** bound YAML alias expansion and stop echoing raw stage errors ([#1641](https://github.com/jentic/jentic-one/issues/1641)) ([36a3eac](https://github.com/jentic/jentic-one/commit/36a3eacfe9cf65136240f0fa33770cdf25011312))
* **registry:** cache catalog previews and cap inline spec size on POST /apis ([#1655](https://github.com/jentic/jentic-one/issues/1655)) ([5f81998](https://github.com/jentic/jentic-one/commit/5f8199891368df38d2f46c75e73e5bcc7c9c2e9e))
* **registry:** pass search config into strategies and map unsupported-at-query errors ([#1652](https://github.com/jentic/jentic-one/issues/1652)) ([7e336f4](https://github.com/jentic/jentic-one/commit/7e336f4d7183e56d1c149d965ca0b7ad5fbe40a9))
* **registry:** resolve or reject relative server URLs at import ([#1777](https://github.com/jentic/jentic-one/issues/1777)) ([39bce16](https://github.com/jentic/jentic-one/commit/39bce166e52a38e5d82ccb23f6a5cece2a5d926c))
* **registry:** tolerate duplicate and over-long operationIds on spec import ([#1639](https://github.com/jentic/jentic-one/issues/1639)) ([3a013f1](https://github.com/jentic/jentic-one/commit/3a013f108540b3b189cf5c89148fb4ab3e93baf9))
* **scoping:** scope management writes to what the caller can see ([#1848](https://github.com/jentic/jentic-one/issues/1848)) ([063c2f9](https://github.com/jentic/jentic-one/commit/063c2f92d87739478dac797546b6ee75c2942e34))
* **scoping:** uniform not-found responses for grants, agent PATCH and late-bind ([#1864](https://github.com/jentic/jentic-one/issues/1864)) ([7be0bf5](https://github.com/jentic/jentic-one/commit/7be0bf5d4d4a518c4593acdbc17979350f49747f))
* **skill:** describe jentic skill capability neutrally instead of steering tool choice ([#1666](https://github.com/jentic/jentic-one/issues/1666)) ([f3b2119](https://github.com/jentic/jentic-one/commit/f3b21199e84b2a7e3946edbebc516e8d89d77cc1)), closes [#996](https://github.com/jentic/jentic-one/issues/996)
* **state:** bound the memory shared-state backend and idempotency store ([#1859](https://github.com/jentic/jentic-one/issues/1859)) ([c68a7c1](https://github.com/jentic/jentic-one/commit/c68a7c108b094ae81ca031a4929d0d2022c44dc0))
* **state:** follow-ups from review of [#1859](https://github.com/jentic/jentic-one/issues/1859) and [#1860](https://github.com/jentic/jentic-one/issues/1860) ([#1869](https://github.com/jentic/jentic-one/issues/1869)) ([aa30f13](https://github.com/jentic/jentic-one/commit/aa30f136fc1b0fc7bb134d4d53c2ef981eb3b142))
* **state:** follow-ups from review of [#1869](https://github.com/jentic/jentic-one/issues/1869) ([#1884](https://github.com/jentic/jentic-one/issues/1884)) ([7c252be](https://github.com/jentic/jentic-one/commit/7c252be4cc18c815aa070f7f7f4e2ac181ad3a32))
* **web:** register overlay routers ahead of the broker catch-all ([#1737](https://github.com/jentic/jentic-one/issues/1737)) ([da83573](https://github.com/jentic/jentic-one/commit/da835735983cf097e9927a2ab7034edbf1364dd3))


### Performance

* **admin:** drop unused toolkit_started index and repeated-failure toolkit axis ([#1921](https://github.com/jentic/jentic-one/issues/1921)) ([b73a2f8](https://github.com/jentic/jentic-one/commit/b73a2f8105007933acf5b582caee11e3dd0b6e0a))
* **admin:** key the repeated-failure scan index on credential_id ([#1741](https://github.com/jentic/jentic-one/issues/1741)) ([749e206](https://github.com/jentic/jentic-one/commit/749e206ff6802f246209a22ea10e229886e71d67)), closes [#1707](https://github.com/jentic/jentic-one/issues/1707)
* **admin:** write hot-path events and execution records with Core INSERT ([#1826](https://github.com/jentic/jentic-one/issues/1826)) ([b338d89](https://github.com/jentic/jentic-one/commit/b338d8929560fba0a250a10badfa500468eb775c))
* **auth:** fold ApiKeyResolver credential and grant reads into one query ([#1948](https://github.com/jentic/jentic-one/issues/1948)) ([9c201b6](https://github.com/jentic/jentic-one/commit/9c201b65d03073ba27dece946b8c57df2ab7471a)), closes [#1944](https://github.com/jentic/jentic-one/issues/1944)
* **auth:** read api key row and grants in one admin session ([#1728](https://github.com/jentic/jentic-one/issues/1728)) ([b221914](https://github.com/jentic/jentic-one/commit/b22191425f11a7fdb1c7cb8ec60ec90bcd3bdc09)), closes [#1703](https://github.com/jentic/jentic-one/issues/1703)
* **broker:** bound concurrent streaming execution persistence ([#1748](https://github.com/jentic/jentic-one/issues/1748)) ([6b8310f](https://github.com/jentic/jentic-one/commit/6b8310f83b973b23f259fbd0e96279b43e9ef1a6))
* **broker:** resolve upstream DNS off the event loop and share one lookup per call ([#1733](https://github.com/jentic/jentic-one/issues/1733)) ([9c273e1](https://github.com/jentic/jentic-one/commit/9c273e174f3b0e4704b80c483129c78090699093))
* **broker:** run cold-path token, rule, and actor-status reads in read_session() ([#1947](https://github.com/jentic/jentic-one/issues/1947)) ([08f01b7](https://github.com/jentic/jentic-one/commit/08f01b70a084d2ac107660a54546878c107938a9))
* **control:** load only the type-matching credential child in list_by_vendor ([#1730](https://github.com/jentic/jentic-one/issues/1730)) ([cdf434e](https://github.com/jentic/jentic-one/commit/cdf434eaa4a773c47a5ac544c2846a2eb39f0d2f))
* **control:** validate OAuth and integration URLs off the event loop ([#1762](https://github.com/jentic/jentic-one/issues/1762)) ([406f0f4](https://github.com/jentic/jentic-one/commit/406f0f4e65d6c4a5471639194de8a87a933f0925))
* **db:** gate pool_pre_ping on connection idle time ([#1937](https://github.com/jentic/jentic-one/issues/1937)) ([57dab69](https://github.com/jentic/jentic-one/commit/57dab69ae64273c2d72f2dbdc14e59432804fed9)), closes [#1935](https://github.com/jentic/jentic-one/issues/1935)
* **db:** make pool_pre_ping configurable ([#1727](https://github.com/jentic/jentic-one/issues/1727)) ([1de5897](https://github.com/jentic/jentic-one/commit/1de58975ed1053cde67fba5bfcc6ec0958ccabeb))
* **db:** run broker hot-path reads in autocommit read sessions ([#1829](https://github.com/jentic/jentic-one/issues/1829)) ([b23b176](https://github.com/jentic/jentic-one/commit/b23b176ecfc8dde7572495b2f833daf51e811e86))
* **deps:** declare sniffio so httpcore stops re-attempting a failed import per lock ([#1726](https://github.com/jentic/jentic-one/issues/1726)) ([359ac3e](https://github.com/jentic/jentic-one/commit/359ac3ee77cea2b24844bcb4ce5462a249fbdec4)), closes [#1697](https://github.com/jentic/jentic-one/issues/1697)
* **monitoring:** load lane for the admin monitoring reads ([#1998](https://github.com/jentic/jentic-one/issues/1998)) ([a87cf13](https://github.com/jentic/jentic-one/commit/a87cf136f30531c566427520ce4859edcfeffae7))
* **registry:** drop deprecated Result.tuples() from the URL lookup hot path ([#1827](https://github.com/jentic/jentic-one/issues/1827)) ([40deaff](https://github.com/jentic/jentic-one/commit/40deaff0eac8fd852b0fa0379d082e466b4f45fd))
* **registry:** join live URL-index candidates on current_revision_id alone ([#1987](https://github.com/jentic/jentic-one/issues/1987)) ([e8e8b02](https://github.com/jentic/jentic-one/commit/e8e8b027414f1cad6703f40d06f7e3c0ffa2d229)), closes [#1976](https://github.com/jentic/jentic-one/issues/1976)
* **registry:** resolve URL to operation identity in one statement ([#1731](https://github.com/jentic/jentic-one/issues/1731)) ([0ce7ecd](https://github.com/jentic/jentic-one/commit/0ce7ecd8a4e86fe18b1b059aef00ae7f24041ce8)), closes [#1699](https://github.com/jentic/jentic-one/issues/1699)
* **tracing:** cut OpenTelemetry cost on the request hot path ([#1825](https://github.com/jentic/jentic-one/issues/1825)) ([6e6dd3a](https://github.com/jentic/jentic-one/commit/6e6dd3a77d303bb25b1ecdd8341ecddddf695553))
* **worker:** configurable job poll interval and concurrency with in-process wake-up ([#1769](https://github.com/jentic/jentic-one/issues/1769)) ([b43654c](https://github.com/jentic/jentic-one/commit/b43654c97c1a33dd72f64daf77a82b0551b72511))


### Refactors

* **admin:** drop the legacy toolkit_id filter from monitoring and executions ([#1745](https://github.com/jentic/jentic-one/issues/1745)) ([6743535](https://github.com/jentic/jentic-one/commit/674353526c29aa6e1276a905cc0ca0f101a2b180))
* **broker:** unify discovery errors and split agent directives out of exceptions ([#1985](https://github.com/jentic/jentic-one/issues/1985)) ([edd9cd3](https://github.com/jentic/jentic-one/commit/edd9cd3ba9f89f13bb5f7698935b9e4bf5427e2a))
* **config:** split shared/config.py into a config package ([#1983](https://github.com/jentic/jentic-one/issues/1983)) ([1d4b535](https://github.com/jentic/jentic-one/commit/1d4b53595a2df860156395386eab88de3711a8d3))
* **control:** remove dead code left by the toolkit/approval-effects removals ([#1664](https://github.com/jentic/jentic-one/issues/1664)) ([fe62ca7](https://github.com/jentic/jentic-one/commit/fe62ca72a0ddb5a31739f0c747ab42a71185719d))
* **shared:** delete dead lookups module and broker url_validation re-export ([#1993](https://github.com/jentic/jentic-one/issues/1993)) ([2e51ea2](https://github.com/jentic/jentic-one/commit/2e51ea28184b50fb798cfe22ed299b346386f6a5))


### Documentation

* **control:** clarify schema requirement in register_access_filter_provider ([#1922](https://github.com/jentic/jentic-one/issues/1922)) ([42e4611](https://github.com/jentic/jentic-one/commit/42e4611826b1eba7427c9b0bfa9bf7a447364bbb))


### Build System

* **deps-dev:** bump @playwright/test in /ui in the testing group ([#2003](https://github.com/jentic/jentic-one/issues/2003)) ([cbd6823](https://github.com/jentic/jentic-one/commit/cbd68235e7e4f8960bc1bd69ca1580e49b4044d7))
* **deps-dev:** bump @testing-library/user-event ([#1782](https://github.com/jentic/jentic-one/issues/1782)) ([6c18d6e](https://github.com/jentic/jentic-one/commit/6c18d6ead16e4e5ad02a23f09479b72cfd622d9b))
* **deps-dev:** bump @types/node ([#1783](https://github.com/jentic/jentic-one/issues/1783)) ([ee45a79](https://github.com/jentic/jentic-one/commit/ee45a7900166f4bdb8632200e667853e785c6583))
* **deps-dev:** bump @vitest/browser from 5.0.4 to 5.0.5 in /ui ([#2000](https://github.com/jentic/jentic-one/issues/2000)) ([a952e3b](https://github.com/jentic/jentic-one/commit/a952e3b79e2c7367c341d7262ebf275b4bf7ef7e))
* **deps-dev:** bump globals from 17.12.0 to 17.13.0 in /ui ([#1784](https://github.com/jentic/jentic-one/issues/1784)) ([c31ae55](https://github.com/jentic/jentic-one/commit/c31ae55d46ad34ff7935673a4a36d0f27d40a49d))
* **deps-dev:** bump pg from 8.23.0 to 8.23.1 in /ui ([#2005](https://github.com/jentic/jentic-one/issues/2005)) ([5927094](https://github.com/jentic/jentic-one/commit/59270941681f5b145e5697c6d0b885fde07bf542))
* **deps-dev:** bump prettier from 3.9.9 to 3.9.10 in /ui ([#2004](https://github.com/jentic/jentic-one/issues/2004)) ([c8af96e](https://github.com/jentic/jentic-one/commit/c8af96ea88a6b937464edf9e8b5ef4f86c7d8a47))
* **deps-dev:** bump the eslint group in /ui with 2 updates ([#1781](https://github.com/jentic/jentic-one/issues/1781)) ([c8984f3](https://github.com/jentic/jentic-one/commit/c8984f35bd06e8ff004eca0955ba9c0f48964974))
* **deps-dev:** bump typescript-eslint in /ui in the eslint group ([#2001](https://github.com/jentic/jentic-one/issues/2001)) ([6ac82d8](https://github.com/jentic/jentic-one/commit/6ac82d8b8c133c870f85ebf2f114522e8189c76e))
* **deps-dev:** bump vite in /ui in the vite group across 1 directory ([#1780](https://github.com/jentic/jentic-one/issues/1780)) ([b5d65ed](https://github.com/jentic/jentic-one/commit/b5d65edab81c0dcdf0d52a907a71b1150ac8ccf7))
* **deps:** bump golang.org/x/text to v0.41.0 for CVE-2026-56851 ([#1882](https://github.com/jentic/jentic-one/issues/1882)) ([793c5d2](https://github.com/jentic/jentic-one/commit/793c5d26a314e7a7b509da245502a7f73b93147b))
* **deps:** bump the python group with 4 updates ([#2006](https://github.com/jentic/jentic-one/issues/2006)) ([f06fa15](https://github.com/jentic/jentic-one/commit/f06fa157ec05589ae48d1bcf9edda49aba7ecbec))
* **deps:** bump the python group with 8 updates ([#1787](https://github.com/jentic/jentic-one/issues/1787)) ([e3dd9f6](https://github.com/jentic/jentic-one/commit/e3dd9f6a29d83228df061f9c107db26a030dc6c3))
* **deps:** bump the react group across 1 directory with 3 updates ([#2002](https://github.com/jentic/jentic-one/issues/2002)) ([7e4fb14](https://github.com/jentic/jentic-one/commit/7e4fb14aacd3dd1128273c23ec1c9ba6da9a7f9c))

## [0.43.0](https://github.com/jentic/jentic-one/compare/v0.42.2...v0.43.0) (2026-10-14)


### Features

* **agents:** hard-delete agents with DELETE /agents/{id}?purge=true ([#1606](https://github.com/jentic/jentic-one/issues/1606)) ([f0183ca](https://github.com/jentic/jentic-one/commit/f0183ca8faa846c46143b0e9d5a49ddb60daa251))
* **broker:** version the broker OpenAPI spec with the release and raise its scorecard ([#1626](https://github.com/jentic/jentic-one/issues/1626)) ([669bf51](https://github.com/jentic/jentic-one/commit/669bf514386514abd6fe37ad3071746e879e4e72))
* **deploy:** add single-container quickstart Dockerfile ([#1621](https://github.com/jentic/jentic-one/issues/1621)) ([be7d2c2](https://github.com/jentic/jentic-one/commit/be7d2c2c5fa664ea50269407b22a42616d1d718c))
* **deploy:** replace quickstart Dockerfile with a compose file ([#1622](https://github.com/jentic/jentic-one/issues/1622)) ([61617cf](https://github.com/jentic/jentic-one/commit/61617cfad4e15e26cf5a997be8419501fa50459a))
* **openapi:** add an example to every control-plane parameter ([#1613](https://github.com/jentic/jentic-one/issues/1613)) ([609963e](https://github.com/jentic/jentic-one/commit/609963e3e14850bdba50721c2989ed04ec334f74))
* **openapi:** declare the OAuth2 authorization-code scheme alongside BearerAuth ([#1624](https://github.com/jentic/jentic-one/issues/1624)) ([b12a974](https://github.com/jentic/jentic-one/commit/b12a974fd89e7e1f98dd85611234fa211cafedbf))
* **openapi:** document a default Problem Details response on every operation ([#1623](https://github.com/jentic/jentic-one/issues/1623)) ([a973824](https://github.com/jentic/jentic-one/commit/a973824f6cd91ef585d3c690aa0b29d9ddabc0a6))
* **openapi:** fix invalid examples and undocumented error bodies in the control spec ([#1611](https://github.com/jentic/jentic-one/issues/1611)) ([5f6c0d7](https://github.com/jentic/jentic-one/commit/5f6c0d7fb72014acfac40e463de977539498316f))


### Bug Fixes

* **admin:** scope user, actor-directory and OAuth grant listings to the caller ([#1608](https://github.com/jentic/jentic-one/issues/1608)) ([259f045](https://github.com/jentic/jentic-one/commit/259f045214b6f196642dc80dc594f93e59715cfe))
* **migrations:** map the retired owner:access-requests:read onto credentials:connect ([#1612](https://github.com/jentic/jentic-one/issues/1612)) ([c9fb863](https://github.com/jentic/jentic-one/commit/c9fb863de0934356959788831b262200134eadbc))


### Documentation

* **broker:** add schema-level and request-body examples to the broker spec ([#1630](https://github.com/jentic/jentic-one/issues/1630)) ([d94a4f9](https://github.com/jentic/jentic-one/commit/d94a4f9f4bd10fddf1e8383630901854a477b387))
* **openapi:** declare the confirm discriminator and mirror examples onto schemas and media ([#1627](https://github.com/jentic/jentic-one/issues/1627)) ([65dacae](https://github.com/jentic/jentic-one/commit/65dacae3f45d5be1c4f3ff4f5ad275580ee9cadd))
* **openapi:** describe every parameter, model field, path item and request body ([#1617](https://github.com/jentic/jentic-one/issues/1617)) ([3828354](https://github.com/jentic/jentic-one/commit/38283540ebc906048ba265398e7487f14f4dce94))
* **openapi:** give every model field and component a realistic example ([#1625](https://github.com/jentic/jentic-one/issues/1625)) ([39e67d3](https://github.com/jentic/jentic-one/commit/39e67d3867c531ea0e1da3d7368b2904a368f95a))
* **openapi:** hide well-known documents, browser pages and duplicate probes from the control spec ([#1629](https://github.com/jentic/jentic-one/issues/1629)) ([a8ef9d8](https://github.com/jentic/jentic-one/commit/a8ef9d87deacd5c124039049a94888f27ada1d2b))
* **openapi:** rewrite summaries and descriptions for 34 operations ([#1614](https://github.com/jentic/jentic-one/issues/1614)) ([b238928](https://github.com/jentic/jentic-one/commit/b2389282ecc3596edf1f25613342475e7caae00f))
* **openapi:** summarise every parameter example and path item ([#1616](https://github.com/jentic/jentic-one/issues/1616)) ([65710df](https://github.com/jentic/jentic-one/commit/65710df0019c30f19bcd4136b401a881cce85408))
* **readme:** add Open Source Alternatives badge ([#1615](https://github.com/jentic/jentic-one/issues/1615)) ([6ea3067](https://github.com/jentic/jentic-one/commit/6ea3067ad3112fc5e069987ba9e50302bf3b0c11))

## [0.42.2](https://github.com/jentic/jentic-one/compare/v0.42.1...v0.42.2) (2026-10-13)


### Bug Fixes

* **admin:** scope audit log reads to the caller and their agents ([#1604](https://github.com/jentic/jentic-one/issues/1604)) ([3014995](https://github.com/jentic/jentic-one/commit/30149956f7194871d9ed9bba8d8c29110edf7f30))
* **broker:** evaluate binding rules on the spec-relative operation path ([#1552](https://github.com/jentic/jentic-one/issues/1552)) ([31e6b8f](https://github.com/jentic/jentic-one/commit/31e6b8f50e7d002056a9435329515711b23ef147))
* **registry:** delete API revisions before the API and explain blocked deletes ([#1605](https://github.com/jentic/jentic-one/issues/1605)) ([459dc06](https://github.com/jentic/jentic-one/commit/459dc060ceb0e3d8900bf584594d5e9f7feb14fa))

## [0.42.1](https://github.com/jentic/jentic-one/compare/v0.42.0...v0.42.1) (2026-10-12)


### Bug Fixes

* **ui:** show who started a job from the job record, not an empty audit lookup ([#1603](https://github.com/jentic/jentic-one/issues/1603)) ([2a16f31](https://github.com/jentic/jentic-one/commit/2a16f318dc70423dc7d2f47d7baa902672107d94))


### Build System

* **deps-dev:** bump the testing group across 1 directory with 3 updates ([#1590](https://github.com/jentic/jentic-one/issues/1590)) ([fa8da91](https://github.com/jentic/jentic-one/commit/fa8da91119c4b6b930dc91a8d05a025c9f51b2eb))
* **deps:** bump framer-motion from 13.4.0 to 14.0.0 in /ui ([#1579](https://github.com/jentic/jentic-one/issues/1579)) ([84bd3eb](https://github.com/jentic/jentic-one/commit/84bd3ebbb33cbef73db1c743e431b831a6faa2a7))
* **deps:** bump the python group with 16 updates ([#1580](https://github.com/jentic/jentic-one/issues/1580)) ([eb3797e](https://github.com/jentic/jentic-one/commit/eb3797e69815c6d8c7adf2ab977c50150fd05b4b))

## [0.42.0](https://github.com/jentic/jentic-one/compare/v0.41.0...v0.42.0) (2026-10-11)


### ⚠ BREAKING CHANGES

* **auth:** rename internal authorization scopes to permissions ([#1399](https://github.com/jentic/jentic-one/issues/1399))
* **agents:** the agent detail page is removed. /app/agents/<id> now redirects to /app/agents?agent=<id>; the ?tab= parameter is ignored.

### Features

* **agents:** redesign the Agents page first run and remove the agent detail page ([#1475](https://github.com/jentic/jentic-one/issues/1475)) ([1de91e4](https://github.com/jentic/jentic-one/commit/1de91e437366eaa0e22551dee5049465e456635a))
* **agents:** redesign the Agents tab — strip, sticky card, rows, cards and access step ([#1570](https://github.com/jentic/jentic-one/issues/1570)) ([fefe78f](https://github.com/jentic/jentic-one/commit/fefe78f2229885f9969f0b571358f0275accd4a7))
* **broker:** link missing-credential denials to the agent's open connect session ([#1565](https://github.com/jentic/jentic-one/issues/1565)) ([ccbda77](https://github.com/jentic/jentic-one/commit/ccbda77316b573b6012e01a59dbe1d62b90137dd))
* **broker:** record operation method + path on executions; steer agents to search-hit targets ([#1381](https://github.com/jentic/jentic-one/issues/1381)) ([2f13db4](https://github.com/jentic/jentic-one/commit/2f13db494179e12f54e931e211c41cc720d826b3))
* **broker:** tell agents which shared OAuth app connects a denied API ([#1560](https://github.com/jentic/jentic-one/issues/1560)) ([24b551b](https://github.com/jentic/jentic-one/commit/24b551b73e99978256eab9369cef1626c529c3ae))
* **cli:** let agents pick a shared OAuth app when connecting ([#1559](https://github.com/jentic/jentic-one/issues/1559)) ([1930115](https://github.com/jentic/jentic-one/commit/1930115598afb6fa61db4c1df2f966c9973a4a22))
* **cli:** let agents start a connect for any registry API (CLI, MCP, broker, skills) ([#1569](https://github.com/jentic/jentic-one/issues/1569)) ([fe3f706](https://github.com/jentic/jentic-one/commit/fe3f70642a6ea2ad3901881e706211507c5ae32c))
* **control:** admin-registered shared OAuth apps for user SSO ([#1435](https://github.com/jentic/jentic-one/issues/1435)) ([e25263a](https://github.com/jentic/jentic-one/commit/e25263af878c7cf32f5c51a826649108e6740706))
* **control:** classify and describe a spec's OAuth scopes, and offer a catalog spec's endpoints to an approver's own app ([#1600](https://github.com/jentic/jentic-one/issues/1600)) ([dc9e66b](https://github.com/jentic/jentic-one/commit/dc9e66b3d7bf57a9b1267f367da82a3630ce83c2))
* **control:** connect sessions for registry APIs with human-entered credentials ([#1567](https://github.com/jentic/jentic-one/issues/1567)) ([66d5fc1](https://github.com/jentic/jentic-one/commit/66d5fc198d37baa5b34bb8332c70ae1f3ffa7c1f))
* **control:** connect-session target kinds, outcomes table and manual-flows gate ([#1566](https://github.com/jentic/jentic-one/issues/1566)) ([4326ff4](https://github.com/jentic/jentic-one/commit/4326ff408eeef6e4744682ffaf376e80a08f946f))
* **control:** let agent owners and org:admin act on connect sessions ([#1561](https://github.com/jentic/jentic-one/issues/1561)) ([15620de](https://github.com/jentic/jentic-one/commit/15620de6f83344e235bb6f9445df90cf7656da5d))
* **control:** let agents ask for a credential for any registry API by default ([#1595](https://github.com/jentic/jentic-one/issues/1595)) ([aa6f85f](https://github.com/jentic/jentic-one/commit/aa6f85fd29b8ea2f8fbde3d7d3c2f9ee90019e1c))
* **events:** remove the event acknowledgement feature ([#1400](https://github.com/jentic/jentic-one/issues/1400)) ([d014401](https://github.com/jentic/jentic-one/commit/d014401faaca8692549eed68f0bd88ed32262d5e))
* **ui:** add a light theme and make it the default ([#1564](https://github.com/jentic/jentic-one/issues/1564)) ([5eb6dfd](https://github.com/jentic/jentic-one/commit/5eb6dfd768cf29ba3a5048424ae90977222b35b8))
* **ui:** approve manual and awaiting-app connect sessions ([#1568](https://github.com/jentic/jentic-one/issues/1568)) ([3a8fbca](https://github.com/jentic/jentic-one/commit/3a8fbcacba69486f2153b735cf5e417977c1f53a))
* **ui:** auto-close a finished connect, open reviews on their own, and mark notifications read ([#1599](https://github.com/jentic/jentic-one/issues/1599)) ([ef99ce9](https://github.com/jentic/jentic-one/commit/ef99ce9f712162db34a0c70bced351573c1efbc0))
* **ui:** catalog, workspace & agents onboarding redesign ([#1476](https://github.com/jentic/jentic-one/issues/1476)) ([ac66cc8](https://github.com/jentic/jentic-one/commit/ac66cc88536d7fb5889840e6c24785a1233d2254))
* **ui:** show toasts bottom-left, clear of every right-hand panel ([#1598](https://github.com/jentic/jentic-one/issues/1598)) ([aabd432](https://github.com/jentic/jentic-one/commit/aabd432c23fc7fb49da1d6a3c3a8b6466b0d8e6d))
* **ui:** surface shared OAuth apps in every add-credential flow ([#1555](https://github.com/jentic/jentic-one/issues/1555)) ([c6d1104](https://github.com/jentic/jentic-one/commit/c6d1104c6bf70b5961c9b3b21b447b7a97a834ae))
* **ui:** token-less connect approval links and a waiting-for-you inbox ([#1562](https://github.com/jentic/jentic-one/issues/1562)) ([9cf776a](https://github.com/jentic/jentic-one/commit/9cf776af7b612e213be159c4f99693304ba443fd))


### Bug Fixes

* **admin:** resolve actor names for any signed-in caller ([#1529](https://github.com/jentic/jentic-one/issues/1529)) ([762887b](https://github.com/jentic/jentic-one/commit/762887b405180d615e645c6a63fe7d22df00f140))
* **admin:** scope event reads to the caller ([#1508](https://github.com/jentic/jentic-one/issues/1508)) ([d9d93bb](https://github.com/jentic/jentic-one/commit/d9d93bba25fdfd496e86a5354b1c70e93dab6848))
* **admin:** scope execution reads to the caller ([#1507](https://github.com/jentic/jentic-one/issues/1507)) ([6d0926a](https://github.com/jentic/jentic-one/commit/6d0926aa8dc9197be40c4ccb33b5f14da11fe4eb))
* **auth:** admit first-time Google Workspace users on OAuth-client SSO ([#1387](https://github.com/jentic/jentic-one/issues/1387)) ([c01db60](https://github.com/jentic/jentic-one/commit/c01db60cb3658f36928c2afb75e086f1335ece29))
* **auth:** answer 404 for agents the caller cannot see on the oauth-grants route ([#1534](https://github.com/jentic/jentic-one/issues/1534)) ([de9a8b3](https://github.com/jentic/jentic-one/commit/de9a8b3a280a8869cbe8a592a0ef11c5a0ce8ca7))
* **auth:** restrict agent approval to the agent's owner or an admin ([#1525](https://github.com/jentic/jentic-one/issues/1525)) ([47b0dc5](https://github.com/jentic/jentic-one/commit/47b0dc58ede4b5117a88f8519369b4cce6ad1ec3))
* **broker:** recompute Content-Length on forwarded requests ([#1524](https://github.com/jentic/jentic-one/issues/1524)) ([2cc283d](https://github.com/jentic/jentic-one/commit/2cc283d3911b5834d7567f2a99dc79360a34a5eb))
* **broker:** replay content and version headers on async executions ([#1583](https://github.com/jentic/jentic-one/issues/1583)) ([a639659](https://github.com/jentic/jentic-one/commit/a6396592149165a10738b42a5fa998b49d492c16))
* **cli:** keep waiting for approval when the server drops an idle connection ([#1596](https://github.com/jentic/jentic-one/issues/1596)) ([3d68f93](https://github.com/jentic/jentic-one/commit/3d68f9346c2a45a22a7d425f61259eef9a2ac357))
* **cli:** tell a pending upgrade step apart from a schema behind head on start ([#1538](https://github.com/jentic/jentic-one/issues/1538)) ([4572f72](https://github.com/jentic/jentic-one/commit/4572f72d04cb1fdb4ca76c46da635134c4281a61))
* **cli:** treat broker resolve failures as errors on execute ([#1429](https://github.com/jentic/jentic-one/issues/1429)) ([#1517](https://github.com/jentic/jentic-one/issues/1517)) ([b29ab5d](https://github.com/jentic/jentic-one/commit/b29ab5de8279e2582e489a1a0c06bee40b1af94d))
* **control:** give vendor-connect credentials the API's registered identity ([#1574](https://github.com/jentic/jentic-one/issues/1574)) ([bb492c2](https://github.com/jentic/jentic-one/commit/bb492c2a08ca989f350b9f62b3dec59d7c5d60f4))
* **control:** limit binding permission reads to caller-visible agents ([#1532](https://github.com/jentic/jentic-one/issues/1532)) ([f9f5a2c](https://github.com/jentic/jentic-one/commit/f9f5a2c1f970f215593aea03fe4db01c11662b51))
* **control:** list only caller-visible agents bound to a credential ([#1522](https://github.com/jentic/jentic-one/issues/1522)) ([6b2f638](https://github.com/jentic/jentic-one/commit/6b2f638cbc1a7aa1b7069df4e90552b61820ebb1))
* **control:** restrict who can attach a permission rule set ([#1528](https://github.com/jentic/jentic-one/issues/1528)) ([14c250e](https://github.com/jentic/jentic-one/commit/14c250e30f46411ef1c757c70864fc78074d7de7))
* **control:** return created_by when creating a credential ([#1521](https://github.com/jentic/jentic-one/issues/1521)) ([7db9139](https://github.com/jentic/jentic-one/commit/7db91391652f7097cbd4d07779998bb0d56c0b32))
* **credentials:** scope a catalog-picked credential to the API its import registered ([#1593](https://github.com/jentic/jentic-one/issues/1593)) ([e810203](https://github.com/jentic/jentic-one/commit/e810203941d47a9d618e9c7f4f68bda586397699))
* **db:** read every ORM timestamp back timezone-aware on SQLite ([#1597](https://github.com/jentic/jentic-one/issues/1597)) ([46f085d](https://github.com/jentic/jentic-one/commit/46f085d7e062f2dc11fa4ba043c35ad60e401bae))
* **events:** name credentials and agents in event summaries ([#1541](https://github.com/jentic/jentic-one/issues/1541)) ([acfcdf4](https://github.com/jentic/jentic-one/commit/acfcdf49120cbeb92d23043a72fa4af17661d8c8))
* **events:** name the credential owner on credential use and health events ([#1523](https://github.com/jentic/jentic-one/issues/1523)) ([0a64f9c](https://github.com/jentic/jentic-one/commit/0a64f9cd9c0ebc89ef355fed8b854a86f193d50d))
* **jobs:** emit import.completed only for import jobs ([#1526](https://github.com/jentic/jentic-one/issues/1526)) ([74ef7cb](https://github.com/jentic/jentic-one/commit/74ef7cb81faec8dae33eb9d8faa8c8894c164eb9))
* **logging:** mask query-string values in access logs ([#1572](https://github.com/jentic/jentic-one/issues/1572)) ([f89fa4d](https://github.com/jentic/jentic-one/commit/f89fa4db758c13e0f9e337aad5d5d23436a66344))
* **migrations:** leave the vector extension in place during lexical search setup ([#1519](https://github.com/jentic/jentic-one/issues/1519)) ([0cefce0](https://github.com/jentic/jentic-one/commit/0cefce0f8ce794d1df717a3927a7e74fab2d8eff))
* **migrations:** rerun the rule-set curation step on every upgrade and report pending steps ([#1536](https://github.com/jentic/jentic-one/issues/1536)) ([da2cc7f](https://github.com/jentic/jentic-one/commit/da2cc7fe9ab294a863c103b7c8d6c50fe626d875))
* **migrations:** sweep oauth_clients.allowed_scopes as a native array on PostgreSQL ([#1544](https://github.com/jentic/jentic-one/issues/1544)) ([53d0002](https://github.com/jentic/jentic-one/commit/53d0002c90f9fd964aa14e2c1dd143a98d8865e3))
* **openapi:** publish placeholder hosts and advertise the deployment's own hosts ([#1504](https://github.com/jentic/jentic-one/issues/1504)) ([7b99c77](https://github.com/jentic/jentic-one/commit/7b99c771761fabe6318e7f8a3f5ae9b8e95d5fc9))
* **registry:** catalog import derives vendor-doubled api_name; warn on unmatched credential scope ([#1067](https://github.com/jentic/jentic-one/issues/1067)) ([b30eb07](https://github.com/jentic/jentic-one/commit/b30eb07ce06e013f9327e09914bbd17c7c18429f))
* **registry:** report declared security schemes from inspect ([#1554](https://github.com/jentic/jentic-one/issues/1554)) ([05994ae](https://github.com/jentic/jentic-one/commit/05994ae615f2bb30d822ee04bb8ebdeff4928b1f)), closes [#1553](https://github.com/jentic/jentic-one/issues/1553)
* **registry:** require operator approval to pin a draft that changes server hosts ([#1573](https://github.com/jentic/jentic-one/issues/1573)) ([f5d9bad](https://github.com/jentic/jentic-one/commit/f5d9bad2123fde0cfc8fbcf4986043c51e0f1ab0))
* **registry:** scope note updates and deletes to the caller ([#1518](https://github.com/jentic/jentic-one/issues/1518)) ([accf0b3](https://github.com/jentic/jentic-one/commit/accf0b3c91dac52f310f1ca6bcf3c2d14583a91e))
* **ui:** clearer not-found and no-access states ([#1537](https://github.com/jentic/jentic-one/issues/1537)) ([56fef88](https://github.com/jentic/jentic-one/commit/56fef88826aa3ff7db577f59aea8d250376ab952))
* **ui:** follow-ups from the post-redesign walk of the agents and library pages ([#1557](https://github.com/jentic/jentic-one/issues/1557)) ([4c404b2](https://github.com/jentic/jentic-one/commit/4c404b23d8698fe323a11fef39ee3500b9063b75))
* **ui:** gate binding pause and tidy no-access copy ([#1540](https://github.com/jentic/jentic-one/issues/1540)) ([765ccdc](https://github.com/jentic/jentic-one/commit/765ccdc59bd0a1ead59069fc6e987224a44fdd70))
* **ui:** give cards an edge on light sheets ([#1592](https://github.com/jentic/jentic-one/issues/1592)) ([ceca792](https://github.com/jentic/jentic-one/commit/ceca7929f9b69a5e2319953cf05198586df0bfd9))
* **ui:** hide actions the caller's permissions don't allow ([#1535](https://github.com/jentic/jentic-one/issues/1535)) ([2567d23](https://github.com/jentic/jentic-one/commit/2567d23fde2caf9e01ab3cb44146c6ea3f861006))
* **ui:** keep the Agents ?agent= write-back from undoing a navigation away ([#1584](https://github.com/jentic/jentic-one/issues/1584)) ([65e7593](https://github.com/jentic/jentic-one/commit/65e75936fb94dc4b2ca684193917d192cefcf540))
* **ui:** keep the client and raw request on the api facade ([#1542](https://github.com/jentic/jentic-one/issues/1542)) ([121495a](https://github.com/jentic/jentic-one/commit/121495a71e6323497760c68e04c8c6d72fb958b3))
* **ui:** keep the connect approval's decision buttons pinned while the review scrolls ([#1594](https://github.com/jentic/jentic-one/issues/1594)) ([c1161b1](https://github.com/jentic/jentic-one/commit/c1161b1add906e30f4a754cffe0f73b488325cb5))
* **ui:** keep the query string when returning from sign-in ([#1571](https://github.com/jentic/jentic-one/issues/1571)) ([9c36f6b](https://github.com/jentic/jentic-one/commit/9c36f6b4f95e43d5600bb51aa35c764084118861))
* **ui:** open tooltips on keyboard focus only, not on focus a sheet moves ([#1591](https://github.com/jentic/jentic-one/issues/1591)) ([3c6c9f5](https://github.com/jentic/jentic-one/commit/3c6c9f550a4ec0592f70cd2346de01a5c7d5a679))
* **ui:** show credentials shared with the caller as read-only ([#1520](https://github.com/jentic/jentic-one/issues/1520)) ([dbc9326](https://github.com/jentic/jentic-one/commit/dbc9326dee61ad100ce822a8ae555de31e11e34e))
* **ui:** show the attached rule set on a credential binding ([#1533](https://github.com/jentic/jentic-one/issues/1533)) ([3be1f47](https://github.com/jentic/jentic-one/commit/3be1f47426b4420936e19d9a5d1fd117cffa0796))
* **ui:** stop event streams and show a clear state for callers without event access ([#1527](https://github.com/jentic/jentic-one/issues/1527)) ([b6478e0](https://github.com/jentic/jentic-one/commit/b6478e0cedacd6178c1f1e014a6c37451e04239c))
* **ui:** wait for the connect-request read in the attention access tests ([#1581](https://github.com/jentic/jentic-one/issues/1581)) ([5a72b50](https://github.com/jentic/jentic-one/commit/5a72b500c44dfebbda3f63d03c891b71ccf68e2e))


### Refactors

* **auth:** rename internal authorization scopes to permissions ([#1399](https://github.com/jentic/jentic-one/issues/1399)) ([3ecdd53](https://github.com/jentic/jentic-one/commit/3ecdd53a9fa0df2066d8d560152c0423cdff5096))
* **control:** extract typed-secret writers from CredentialService.create ([#1563](https://github.com/jentic/jentic-one/issues/1563)) ([9323ce6](https://github.com/jentic/jentic-one/commit/9323ce6ce646280fbf23a7fa76f578cd5b89c5c9))


### Documentation

* **changelog:** add the 0.41.1 hotfix entry ([#1549](https://github.com/jentic/jentic-one/issues/1549)) ([8083c1d](https://github.com/jentic/jentic-one/commit/8083c1d0955b598e1902dfea208e72f5e36f5fc6))
* **releasing:** add the upgrading-to-0.42.0 notes ([#1589](https://github.com/jentic/jentic-one/issues/1589)) ([0ff9047](https://github.com/jentic/jentic-one/commit/0ff90479349658c4f6465ca3ff682bff3e1e1233))
* **skills:** point agents at whoami and the security-schemes route ([#1558](https://github.com/jentic/jentic-one/issues/1558)) ([8bfe7b2](https://github.com/jentic/jentic-one/commit/8bfe7b2b025e0c10cf28a38f97301916e2d19665))
* **skills:** tell agents private upstream targets need an operator allowlist ([#1551](https://github.com/jentic/jentic-one/issues/1551)) ([c58c020](https://github.com/jentic/jentic-one/commit/c58c02069397bf9b89159cebb814eddbb4293d56))
* **telemetry:** enable anonymous telemetry in the shipped install examples ([#1545](https://github.com/jentic/jentic-one/issues/1545)) ([6e1aa2e](https://github.com/jentic/jentic-one/commit/6e1aa2e4eaaa4191f70dae4a4fe745aaa44826d1))


### Build System

* **deps-dev:** bump @types/node ([#1578](https://github.com/jentic/jentic-one/issues/1578)) ([5799d8f](https://github.com/jentic/jentic-one/commit/5799d8f285e6895f479a5de2f48d1b7811d9de42))
* **deps-dev:** bump @types/node in /ui in the types group ([#1514](https://github.com/jentic/jentic-one/issues/1514)) ([ea097aa](https://github.com/jentic/jentic-one/commit/ea097aa0a0fbb91a14d43fdc39a382dc3cd85265))
* **deps-dev:** bump handlebars from 4.7.9 to 4.7.10 in /ui ([#1586](https://github.com/jentic/jentic-one/issues/1586)) ([a7a0f95](https://github.com/jentic/jentic-one/commit/a7a0f9595c3df3b2b7b2e6f3ae66c09d92297a20))
* **deps-dev:** bump prettier from 3.9.8 to 3.9.9 in /ui ([#1515](https://github.com/jentic/jentic-one/issues/1515)) ([967392d](https://github.com/jentic/jentic-one/commit/967392d8dbaf07a668b03cf8dac5afdda45eecc8))
* **deps-dev:** bump sharp from 0.35.4 to 0.35.5 in /ui ([#1585](https://github.com/jentic/jentic-one/issues/1585)) ([9eb8861](https://github.com/jentic/jentic-one/commit/9eb8861c2127fa4d3baaba59261f15e0dae48146))
* **deps-dev:** bump source-map-js from 1.2.1 to 1.2.2 in /ui ([#1587](https://github.com/jentic/jentic-one/issues/1587)) ([e36dabb](https://github.com/jentic/jentic-one/commit/e36dabb801054bbd3c61d4bc743f1f5584759592))
* **deps-dev:** bump the vite group across 1 directory with 6 updates ([#1576](https://github.com/jentic/jentic-one/issues/1576)) ([2782cb0](https://github.com/jentic/jentic-one/commit/2782cb0f719431e065d94074201bbdc7b0b2a97b))
* **deps-dev:** bump the vite group in /ui with 5 updates ([#1511](https://github.com/jentic/jentic-one/issues/1511)) ([9cd0966](https://github.com/jentic/jentic-one/commit/9cd0966d513300d49cb46c3bd46db77d7f68f32e))
* **deps-dev:** bump typescript-eslint ([#1512](https://github.com/jentic/jentic-one/issues/1512)) ([f7f8e05](https://github.com/jentic/jentic-one/commit/f7f8e05ed0b637a49fe57b93e4ad233cdd997670))
* **deps-dev:** bump typescript-eslint ([#1577](https://github.com/jentic/jentic-one/issues/1577)) ([be92fc0](https://github.com/jentic/jentic-one/commit/be92fc080401c81db4697c68c1e9f9dcb1c8c390))
* **deps:** bump fastapi from 0.141.1 to 0.142.0 in the python group ([#1516](https://github.com/jentic/jentic-one/issues/1516)) ([ba0e7b4](https://github.com/jentic/jentic-one/commit/ba0e7b4f49e3f4da18416ada695dd3b8a0213af8))
* **deps:** bump mako from 1.3.12 to 1.4.2 ([#1588](https://github.com/jentic/jentic-one/issues/1588)) ([d5125be](https://github.com/jentic/jentic-one/commit/d5125be56c0475aa5d52d1279901cd952ebeb3ef))
* **deps:** bump pyjwt from 2.14.0 to 2.15.0 ([#1502](https://github.com/jentic/jentic-one/issues/1502)) ([e6327df](https://github.com/jentic/jentic-one/commit/e6327df3230e31af1dff6174d604b844a4cc54e8))

## [0.41.1](https://github.com/jentic/jentic-one/compare/v0.41.0...v0.41.1) (2026-10-07)

Hotfix release cut from v0.41.0 (not main). It fixes the admin scope sweep
that failed a PostgreSQL upgrade when an OAuth client's allowlist still listed
a retired scope, and it scopes execution and event reads to the caller,
without shipping main's in-flight work.

### Bug Fixes

* **migrations:** sweep oauth_clients.allowed_scopes as a native array on PostgreSQL ([#1546](https://github.com/jentic/jentic-one/issues/1546))
* **admin:** scope execution and event reads to the caller ([#1547](https://github.com/jentic/jentic-one/issues/1547))

## [0.41.0](https://github.com/jentic/jentic-one/compare/v0.40.1...v0.41.0) (2026-09-30)


### ⚠ BREAKING CHANGES

* **cli:** remove the service-account mode alias; use --mode agent or JENTIC_MODE=agent (BC-12)
* **auth:** retire sak_ service-account keys and drop the service-account tables (theme 8, phase 4); the upgrade migrates remaining service accounts to agents, and callers must switch to a jak_ key for each successor agent before upgrading
* **control:** the toolkit tables are dropped. Before upgrading, run `flatten-toolkits --verify --acknowledge` on the 0.41 image and retire any live toolkit key on 0.40.x. `broker.direct_bindings_enabled: false` is no longer accepted.

### Features

* **auth:** retire sak_ service-account keys and drop the service-account tables (theme 8, phase 4); the upgrade migrates remaining service accounts to agents, and callers must switch to a jak_ key for each successor agent before upgrading ([4c04ed9](https://github.com/jentic/jentic-one/commit/4c04ed9c2be3930fe625d5d4b4b57db0422188ed))
* **control:** drop the toolkit tables (theme 5, phase 6b) ([#1498](https://github.com/jentic/jentic-one/issues/1498)) ([bd5f490](https://github.com/jentic/jentic-one/commit/bd5f49096042ab94528748a300a78f0955cadbd8))


### Bug Fixes

* **control:** stop counting superseded successors as SA digest mismatches ([#1497](https://github.com/jentic/jentic-one/issues/1497)) ([c48704a](https://github.com/jentic/jentic-one/commit/c48704aec4a57f557604aca6f7b10426136aa08d)), closes [#1416](https://github.com/jentic/jentic-one/issues/1416)


### Refactors

* **cli:** collapse the service-account mode into agent (theme-8 D4) ([#1410](https://github.com/jentic/jentic-one/issues/1410)) ([68871ff](https://github.com/jentic/jentic-one/commit/68871ff4e1ff6caef2c189e6317ff03ce7a51046))
* **cli:** remove the service-account mode alias; use --mode agent or JENTIC_MODE=agent (BC-12) ([4c04ed9](https://github.com/jentic/jentic-one/commit/4c04ed9c2be3930fe625d5d4b4b57db0422188ed))


### Build System

* **deps-dev:** bump brace-expansion from 1.1.18 to 1.1.21 in /ui ([#1496](https://github.com/jentic/jentic-one/issues/1496)) ([2d22c84](https://github.com/jentic/jentic-one/commit/2d22c8465b68868d21a75cb4999831c903dddc3b))
* **deps:** bump @tanstack/react-query from 5.102.8 to 5.103.2 in /ui ([#1450](https://github.com/jentic/jentic-one/issues/1450)) ([aa267f5](https://github.com/jentic/jentic-one/commit/aa267f5e5cf7b3010cfeead2f75e59d4fb5443df))
* **deps:** bump pyjwt from 2.13.0 to 2.14.0 ([#1493](https://github.com/jentic/jentic-one/issues/1493)) ([5666faf](https://github.com/jentic/jentic-one/commit/5666faffade00c1a51ee4677c8fa656d585b5f2d))
* **deps:** bump tailwind-merge from 3.6.0 to 3.7.0 in /ui ([#1449](https://github.com/jentic/jentic-one/issues/1449)) ([1700f01](https://github.com/jentic/jentic-one/commit/1700f0197de12219baf5e927459b9555600ab549))
* **deps:** bump the python group with 10 updates ([#1494](https://github.com/jentic/jentic-one/issues/1494)) ([a23a2ed](https://github.com/jentic/jentic-one/commit/a23a2edc7ba425e6a99c192f2f45a805ece2c795))
* **deps:** bump urllib3 from 2.7.0 to 2.8.0 ([#1501](https://github.com/jentic/jentic-one/issues/1501)) ([c50bb2e](https://github.com/jentic/jentic-one/commit/c50bb2e1aa6af5cbc15ebedb6e16fbf94bf62f97))

## [0.40.1](https://github.com/jentic/jentic-one/compare/v0.40.0...v0.40.1) (2026-09-29)


### Bug Fixes

* **broker:** match credentials on the request's resolved server variables ([#1486](https://github.com/jentic/jentic-one/issues/1486)) ([9fd4879](https://github.com/jentic/jentic-one/commit/9fd48798538e286a1b29a47e77b7ec2abc6e1db6))
* **broker:** re-check agent and credential authorization when a queued execution runs ([#1478](https://github.com/jentic/jentic-one/issues/1478)) ([8360645](https://github.com/jentic/jentic-one/commit/83606456f405c86ac0eb4657f6ad3a956a7ecde4))
* **cli:** run the MCP service account from a root-owned binary copy ([#1482](https://github.com/jentic/jentic-one/issues/1482)) ([e096956](https://github.com/jentic/jentic-one/commit/e09695639a8f9b3ad3975b7d14ef56bca8c95a97))
* **control:** store connect-session poll tokens hashed ([#1477](https://github.com/jentic/jentic-one/issues/1477)) ([0381126](https://github.com/jentic/jentic-one/commit/03811268bce8c032e569822f6a0c616ce7798b17))
* **helm:** mount only the app secrets each surface reads ([#1484](https://github.com/jentic/jentic-one/issues/1484)) ([3eda549](https://github.com/jentic/jentic-one/commit/3eda549698cde57bf118a460632ce70ce1f294aa))
* **registry:** derive vendors from the registrable domain using the Public Suffix List ([#1483](https://github.com/jentic/jentic-one/issues/1483)) ([0c240b4](https://github.com/jentic/jentic-one/commit/0c240b47f11e982534d9a84047422a289e0c6775))
* **registry:** hold server-host changes on credential-bound APIs for review ([#1488](https://github.com/jentic/jentic-one/issues/1488)) ([60a1b61](https://github.com/jentic/jentic-one/commit/60a1b616c38de528444c54ebf92efe14d2bcefe7))
* **registry:** index URLs per revision and serve only live revisions ([#1487](https://github.com/jentic/jentic-one/issues/1487)) ([4d6b8a9](https://github.com/jentic/jentic-one/commit/4d6b8a99219c88228ba98a357a8f8c255f313b2d))
* **registry:** return the canonical API name from inspect ([#1480](https://github.com/jentic/jentic-one/issues/1480)) ([d649879](https://github.com/jentic/jentic-one/commit/d649879fc34e1c30ea3c23495967e7f7cceb1927))
* **registry:** suspend agent credential bindings when their API is deleted ([#1485](https://github.com/jentic/jentic-one/issues/1485)) ([5ce38ae](https://github.com/jentic/jentic-one/commit/5ce38ae4e411f24e7d6e52e49ef1934391116f89))

## [0.40.0](https://github.com/jentic/jentic-one/compare/v0.39.0...v0.40.0) (2026-09-29)


### ⚠ BREAKING CHANGES

* **platform:** credentials & approvals overhaul — retire access requests and service accounts, redesign Agents/Monitor UI ([#1458](https://github.com/jentic/jentic-one/issues/1458))
* remove toolkits — direct agent-credential bindings (theme 5, phases 0-6a) ([#1370](https://github.com/jentic/jentic-one/issues/1370))

### Features

* **admin:** hard delete for OAuth clients ([#1344](https://github.com/jentic/jentic-one/issues/1344)) ([52dc270](https://github.com/jentic/jentic-one/commit/52dc2704859cb10abff9eed4dd16febf55009fd3))
* **auth:** accept form-encoded bodies on /oauth/introspect ([#564](https://github.com/jentic/jentic-one/issues/564)) ([bb43ec9](https://github.com/jentic/jentic-one/commit/bb43ec9c40a68859b75e2ad50b8cc6d1a3a9d07f))
* **auth:** inline agent creation on the zero-agents consent page ([#1332](https://github.com/jentic/jentic-one/issues/1332)) ([be4cb75](https://github.com/jentic/jentic-one/commit/be4cb756b108cb8429ca17cf923d23041a7f2a38))
* **broker:** unregistered_url_handler seam on AppContainer for discovery misses ([#1281](https://github.com/jentic/jentic-one/issues/1281)) ([012d3f0](https://github.com/jentic/jentic-one/commit/012d3f026cf14fdd2a8b63df1b4488a0d341594b))
* **cli:** support multipart/form-data bodies in execute ([#1317](https://github.com/jentic/jentic-one/issues/1317)) ([8348cf8](https://github.com/jentic/jentic-one/commit/8348cf84cc16f2dba77d4ee6a59dc953c1c69f30))
* **config:** EncryptionKey material sources (env/file) + one-shot config caching ([#1286](https://github.com/jentic/jentic-one/issues/1286)) ([8fb8102](https://github.com/jentic/jentic-one/commit/8fb810246afd0f7ecdde8b96b870356440e0d4b3))
* **helm:** give the chart a production shape ([#1396](https://github.com/jentic/jentic-one/issues/1396)) ([6d65741](https://github.com/jentic/jentic-one/commit/6d65741bc19f6e73d28ad48feea8b4052f3f0ed3))
* **instance:** expose the MCP broker URL via /instance and both MCP UIs ([#1338](https://github.com/jentic/jentic-one/issues/1338)) ([5651b8e](https://github.com/jentic/jentic-one/commit/5651b8ed502503bb934a2f2f4fc5b2c96c4a7739)), closes [#1249](https://github.com/jentic/jentic-one/issues/1249)
* **mcp:** serve import_api on the daemon-native /mcp mount ([#1326](https://github.com/jentic/jentic-one/issues/1326)) ([4c5a602](https://github.com/jentic/jentic-one/commit/4c5a60280a3de9111c918ebd7043fef2a5dd2c10))
* **mcp:** serve request_access on the daemon-native /mcp mount ([#1331](https://github.com/jentic/jentic-one/issues/1331)) ([9cbef66](https://github.com/jentic/jentic-one/commit/9cbef6673e2b514954c7d051125f90bb571782ad))
* **mcp:** serve the skill set as resources on the /mcp mount ([#1335](https://github.com/jentic/jentic-one/issues/1335)) ([e0561fe](https://github.com/jentic/jentic-one/commit/e0561fe9f14949087dea77d861540b192a2d4c6d))
* **platform:** credentials & approvals overhaul — retire access requests and service accounts, redesign Agents/Monitor UI ([#1458](https://github.com/jentic/jentic-one/issues/1458)) ([553b865](https://github.com/jentic/jentic-one/commit/553b865fa8a51274b9eaefda184b3f9c6a5c0937))
* **registry:** identity-scoped GET /governed-hosts digest endpoint ([#1283](https://github.com/jentic/jentic-one/issues/1283)) ([2013cc3](https://github.com/jentic/jentic-one/commit/2013cc3eda3909dbc0e2cb6131c2b6aaa2221149))
* remove toolkits — direct agent-credential bindings (theme 5, phases 0-6a) ([#1370](https://github.com/jentic/jentic-one/issues/1370)) ([5d154c5](https://github.com/jentic/jentic-one/commit/5d154c54f0887786007932944dce8d555f30628d))
* **skills:** per-audience jentic skill — router SKILL.md + lane references, served on every surface ([#1336](https://github.com/jentic/jentic-one/issues/1336)) ([3d68fab](https://github.com/jentic/jentic-one/commit/3d68fab467a82d4439b35f30df31628cb43ea3f5))
* **ui:** hard-delete OAuth clients from the danger zone + Disable/Enable vocabulary ([#1346](https://github.com/jentic/jentic-one/issues/1346)) ([6ef8614](https://github.com/jentic/jentic-one/commit/6ef8614887bc2f5463ba71707a3636967f69eeed))
* **ui:** rebuild the OAuth clients settings surface ([#1318](https://github.com/jentic/jentic-one/issues/1318)) ([01e4777](https://github.com/jentic/jentic-one/commit/01e4777e8d28f200f5b5834b8a7567f23158d17a))
* **ui:** render agent_status dormancy marker on OAuth grant rows ([#1358](https://github.com/jentic/jentic-one/issues/1358)) ([b7801b0](https://github.com/jentic/jentic-one/commit/b7801b0d42f1248eb7600b2593e079222e1153b9))
* **ui:** show the deployment's MCP endpoint on the Settings page ([#1330](https://github.com/jentic/jentic-one/issues/1330)) ([6e80e40](https://github.com/jentic/jentic-one/commit/6e80e4072751a847ab2618d7b8ed2ebfa896f2a7))


### Bug Fixes

* **admin:** scope job reads and cancellation to the owning actor ([#1461](https://github.com/jentic/jentic-one/issues/1461)) ([db6293a](https://github.com/jentic/jentic-one/commit/db6293af0ae226e94797dec7dcc5fea85846a294))
* **admin:** strict IdP email_verified parsing and guards on managing other users ([#1462](https://github.com/jentic/jentic-one/issues/1462)) ([1b682d2](https://github.com/jentic/jentic-one/commit/1b682d2f616a0d17cc076954ecff6f6f67bb1888))
* **auth:** accept only first-party sessions on session continue ([#1466](https://github.com/jentic/jentic-one/issues/1466)) ([30931e2](https://github.com/jentic/jentic-one/commit/30931e29c3849a21e6f7a90065dbb1eaacfe8449))
* **auth:** advertise revocation auth method none in the root RFC 8414 doc ([#1328](https://github.com/jentic/jentic-one/issues/1328)) ([9152c0f](https://github.com/jentic/jentic-one/commit/9152c0fafc6792cda2f5e06489090f674fae7b43))
* **auth:** enforce a scope ceiling and owner scoping on agent writes ([#1463](https://github.com/jentic/jentic-one/issues/1463)) ([b5f0e7f](https://github.com/jentic/jentic-one/commit/b5f0e7fb5cc055f26700c65c32c3ecf561113f4c))
* **auth:** keep grants dormant on agent disable and make listings honest ([#1345](https://github.com/jentic/jentic-one/issues/1345)) ([5f93475](https://github.com/jentic/jentic-one/commit/5f934756422d31421f127a304317ae132648ef3b))
* **auth:** revoke oauth consent grants when an agent is archived ([#1340](https://github.com/jentic/jentic-one/issues/1340)) ([27b4e97](https://github.com/jentic/jentic-one/commit/27b4e97dfdc62b1d9d0025ceff3d8cb2e94d9fa1)), closes [#1233](https://github.com/jentic/jentic-one/issues/1233)
* **auth:** speak RFC 6749 §5.2 errors on the token endpoint ([#1339](https://github.com/jentic/jentic-one/issues/1339)) ([9dd1f8a](https://github.com/jentic/jentic-one/commit/9dd1f8a9b7113665ef1a370c662abeb292c98813))
* **cli:** don't block on idle non-TTY stdin in execute ([#1354](https://github.com/jentic/jentic-one/issues/1354)) ([#1361](https://github.com/jentic/jentic-one/issues/1361)) ([5f07a67](https://github.com/jentic/jentic-one/commit/5f07a67f7e70494f26afd955484f803258f6ce5d))
* **cli:** keep credentials on their origin across redirects ([#1469](https://github.com/jentic/jentic-one/issues/1469)) ([d3806c0](https://github.com/jentic/jentic-one/commit/d3806c0564508d920bb45ffcc08b8a02aa2d7142))
* **cli:** parse JSON-RPC envelope strictly in the MCP HTTP pre-auth check ([#1467](https://github.com/jentic/jentic-one/issues/1467)) ([81b9e6e](https://github.com/jentic/jentic-one/commit/81b9e6ef53a1d432f42083dbce2a23f930fcdc07))
* **cli:** refuse --token-file with --allow-unauthenticated on jentic mcp --http ([#1329](https://github.com/jentic/jentic-one/issues/1329)) ([59b6dea](https://github.com/jentic/jentic-one/commit/59b6dea3b9f6607bc221b9cf69bdef43a6b9e54d))
* **cli:** run local-agent probes without startup files and harden privileged file handling ([#1472](https://github.com/jentic/jentic-one/issues/1472)) ([00ea3a2](https://github.com/jentic/jentic-one/commit/00ea3a2ac14c7b25791edf4c74919f879b2cec4c))
* **cli:** stop TestListenerFromFD leaking a dup that closes recycled fds ([#1350](https://github.com/jentic/jentic-one/issues/1350)) ([6aea232](https://github.com/jentic/jentic-one/commit/6aea232cf17fc003cdcbbfb8a19feef5e88e4cd1))
* **config,control:** derive OAuth redirect_uri from public origin ([#818](https://github.com/jentic/jentic-one/issues/818)) ([#887](https://github.com/jentic/jentic-one/issues/887)) ([9c1ad6f](https://github.com/jentic/jentic-one/commit/9c1ad6f2efff8d51b52d79ca1f85ad0d258d4e25))
* **control:** require credential ownership for binding rule writes and resume ([#1471](https://github.com/jentic/jentic-one/issues/1471)) ([d33e7a9](https://github.com/jentic/jentic-one/commit/d33e7a912bde2f41eab17545d076009c5293546e))
* **events:** document and enforce event severity classification ([#907](https://github.com/jentic/jentic-one/issues/907)) ([#1397](https://github.com/jentic/jentic-one/issues/1397)) ([503e1cb](https://github.com/jentic/jentic-one/commit/503e1cb245fa324ddd809df752a19e94fe1941e0))
* **logging:** mask query-string values in outbound URL logs and spans ([#1473](https://github.com/jentic/jentic-one/issues/1473)) ([1c7273f](https://github.com/jentic/jentic-one/commit/1c7273fdb1c3ac373fb3e73a37dcae3c2677115b))
* **mcp:** drop dangling next_tool pointers on the HTTP lane ([#1327](https://github.com/jentic/jentic-one/issues/1327)) ([a418a70](https://github.com/jentic/jentic-one/commit/a418a70c68305ee4900173414a56f339a6f6ec43))
* **mcp:** make served tool descriptions and actionable prose lane-true ([#1347](https://github.com/jentic/jentic-one/issues/1347)) ([7898d11](https://github.com/jentic/jentic-one/commit/7898d11174726ddb593c1869eb1d957a3c65a8cb))
* **migrations:** run the toolkit flatten and key retirement inside the migration run ([#1411](https://github.com/jentic/jentic-one/issues/1411)) ([cd17532](https://github.com/jentic/jentic-one/commit/cd175328a7adc083b1005efe72f3add15adffed0))
* **registry:** resolve URL-index lookups to the canonical API name ([#1460](https://github.com/jentic/jentic-one/issues/1460)) ([03da711](https://github.com/jentic/jentic-one/commit/03da711c72f9524a4bd2bf0f8e89e455920bea93))
* **security:** classify embedded IPv4 and non-global ranges in egress checks ([#1470](https://github.com/jentic/jentic-one/issues/1470)) ([062705b](https://github.com/jentic/jentic-one/commit/062705b50b2849198b7d42967cfba0f90ea49c6d))
* **security:** keep submitted and injected values out of error details ([#1464](https://github.com/jentic/jentic-one/issues/1464)) ([ddc546b](https://github.com/jentic/jentic-one/commit/ddc546b587189da6687c7f6358777b966507fa87))
* **security:** scope credential binds to the owner and bound the toolkit injection path ([#1409](https://github.com/jentic/jentic-one/issues/1409)) ([976e312](https://github.com/jentic/jentic-one/commit/976e31239b199847b31612f05beedfff9b9f968c))
* **setup.sh:** retry db readiness through postgres restart window ([#1357](https://github.com/jentic/jentic-one/issues/1357)) ([2f9af4f](https://github.com/jentic/jentic-one/commit/2f9af4fc00e21aa1f2ca5ba1041e3fbd1b32ba17))
* **tracing:** keep exception text and request data out of exported spans ([#1474](https://github.com/jentic/jentic-one/issues/1474)) ([521c9d9](https://github.com/jentic/jentic-one/commit/521c9d959c197c653068bcb640a3559acff9fe38))
* **ui:** make the agent rail a containing block to stop phantom page scroll ([#1321](https://github.com/jentic/jentic-one/issues/1321)) ([e6c01c7](https://github.com/jentic/jentic-one/commit/e6c01c70e3d261181e190df9b48f748d0f1d8be9))
* **upgrade:** report elevated grants and cross-owner bindings carried over by the upgrade ([#1465](https://github.com/jentic/jentic-one/issues/1465)) ([aab9827](https://github.com/jentic/jentic-one/commit/aab982700b507f597bd570603114fc76860e9c42))


### Documentation

* **api:** align lifecycle vocabulary across endpoint summaries and docs ([#1348](https://github.com/jentic/jentic-one/issues/1348)) ([5a11e00](https://github.com/jentic/jentic-one/commit/5a11e0069c5416cde9decf8d623c092d50a875a0))
* **installation:** production install guides, docs restructure, and CLI package channels ([#1142](https://github.com/jentic/jentic-one/issues/1142)) ([96a2f6d](https://github.com/jentic/jentic-one/commit/96a2f6d454814d070cb884b39d9e803474cb5882))


### Build System

* **deps-dev:** bump @playwright/test in /ui in the testing group ([#1364](https://github.com/jentic/jentic-one/issues/1364)) ([a5b903a](https://github.com/jentic/jentic-one/commit/a5b903a852bb45fbcef5f2c7006f1d051c7a56ae))
* **deps-dev:** bump @types/node in /ui in the types group ([#1365](https://github.com/jentic/jentic-one/issues/1365)) ([a7d3eb3](https://github.com/jentic/jentic-one/commit/a7d3eb3abf7467364dab2fa16ee742026709683e))
* **deps-dev:** bump @types/node in /ui in the types group ([#1391](https://github.com/jentic/jentic-one/issues/1391)) ([486fa86](https://github.com/jentic/jentic-one/commit/486fa8621821f2882daabde1dc29f3772baf07b7))
* **deps-dev:** bump @types/node in /ui in the types group ([#1447](https://github.com/jentic/jentic-one/issues/1447)) ([96589f0](https://github.com/jentic/jentic-one/commit/96589f062b052661130147103150fc8d04f46ae4))
* **deps-dev:** bump prettier from 3.9.6 to 3.9.8 in /ui ([#1448](https://github.com/jentic/jentic-one/issues/1448)) ([e7e4415](https://github.com/jentic/jentic-one/commit/e7e4415d3a296e4931179e34681413f737ed8ec4))
* **deps-dev:** bump the python group with 2 updates ([#1451](https://github.com/jentic/jentic-one/issues/1451)) ([e59359c](https://github.com/jentic/jentic-one/commit/e59359c6c7bcb7511adea3b961f854d474945123))
* **deps-dev:** bump the vite group in /ui with 5 updates ([#1389](https://github.com/jentic/jentic-one/issues/1389)) ([eccc9d7](https://github.com/jentic/jentic-one/commit/eccc9d72f5dfc28eea8af73b4fae145e23726ec4))
* **deps-dev:** bump typescript-eslint in /ui in the eslint group ([#1363](https://github.com/jentic/jentic-one/issues/1363)) ([183eb0f](https://github.com/jentic/jentic-one/commit/183eb0f28232a0655ae02c73d36f0ccc1e8a3e81))
* **deps-dev:** bump typescript-eslint in /ui in the eslint group ([#1446](https://github.com/jentic/jentic-one/issues/1446)) ([b937f68](https://github.com/jentic/jentic-one/commit/b937f68990530f91116115a209a599decc9ed846))
* **deps:** bump anyio from 4.13.0 to 4.14.2 ([#1395](https://github.com/jentic/jentic-one/issues/1395)) ([1f623aa](https://github.com/jentic/jentic-one/commit/1f623aae7980f2636e82a3291843895e8d77e321))
* **deps:** bump framer-motion from 13.2.0 to 13.4.0 in /ui ([#1393](https://github.com/jentic/jentic-one/issues/1393)) ([4a394bf](https://github.com/jentic/jentic-one/commit/4a394bfef529321e06c61dd8ab5ab8bef7472c23))
* **deps:** bump lucide-react from 1.40.0 to 1.43.0 in /ui ([#1366](https://github.com/jentic/jentic-one/issues/1366)) ([c522380](https://github.com/jentic/jentic-one/commit/c5223807b0d6e5778493fa1ab4c50d90854bb60f))
* **deps:** bump lucide-react from 1.43.0 to 1.47.0 in /ui ([#1392](https://github.com/jentic/jentic-one/issues/1392)) ([7e8b00a](https://github.com/jentic/jentic-one/commit/7e8b00aac681a1078f0497918f1270806cf8605e))
* **deps:** bump the python group with 3 updates ([#1367](https://github.com/jentic/jentic-one/issues/1367)) ([57230b0](https://github.com/jentic/jentic-one/commit/57230b0ba6b12ae648092e82edbf980da96c566d))
* **deps:** bump the python group with 4 updates ([#1394](https://github.com/jentic/jentic-one/issues/1394)) ([1e48c2e](https://github.com/jentic/jentic-one/commit/1e48c2ef6db86b1e1891e9f9913adb16a55c0270))
* **deps:** bump the react group across 1 directory with 5 updates ([#1390](https://github.com/jentic/jentic-one/issues/1390)) ([0f5a9ec](https://github.com/jentic/jentic-one/commit/0f5a9ecbdd8803cfa32f2e46f72e89e8c20b0f65))

## [0.39.1](https://github.com/jentic/jentic-one/compare/v0.39.0...v0.39.1) (2026-09-23)

Hotfix release cut from v0.39.0 (not main): bounds broker credential
resolution to the toolkit an execution is authorized against, and picks up
the anyio security update, without shipping main's in-flight work.

### Bug Fixes

* **broker:** bound credential resolution to the selected toolkit ([#1401](https://github.com/jentic/jentic-one/issues/1401))

### Build System

* **deps:** bump anyio from 4.13.0 to 4.14.2 ([#1395](https://github.com/jentic/jentic-one/issues/1395))

## [0.39.0](https://github.com/jentic/jentic-one/compare/v0.38.0...v0.39.0) (2026-09-08)


### ⚠ BREAKING CHANGES

* **auth:** deployments that relied on the old default (DCR clients auto-approved at registration) must now either set server.mcp.oauth.auto_approve_clients: true explicitly or approve pending clients through the admin queue.

### Features

* **auth:** approval-in-flow page for pending OAuth clients on /authorize ([#1264](https://github.com/jentic/jentic-one/issues/1264)) ([a44cee6](https://github.com/jentic/jentic-one/commit/a44cee6c6f7f96b819b81b3476e30c45baffe405))
* **auth:** default MCP OAuth DCR to admin approval, not auto-approve ([#1247](https://github.com/jentic/jentic-one/issues/1247)) ([dccb57d](https://github.com/jentic/jentic-one/commit/dccb57d0995bfcfa29095919c06ba65a34cfb740))
* **auth:** local-account login form on the /authorize flow ([#1285](https://github.com/jentic/jentic-one/issues/1285)) ([4850b2b](https://github.com/jentic/jentic-one/commit/4850b2bfb5895a462ba1a18b73553a75e737642c))
* **auth:** platform-consistent styling for OAuth pages (login + consent) ([#1314](https://github.com/jentic/jentic-one/issues/1314)) ([1bfe7fd](https://github.com/jentic/jentic-one/commit/1bfe7fdcf3487ec664677a118df8860a7fc847b2))
* **auth:** platform-session reuse on /authorize (identity-ladder rung 1) ([#1300](https://github.com/jentic/jentic-one/issues/1300)) ([aec21ce](https://github.com/jentic/jentic-one/commit/aec21ce560be622cb51a00f641c7e7a59fa3e19b))
* **auth:** RFC 7009 token revocation for MCP OAuth clients (closes G11) ([#1237](https://github.com/jentic/jentic-one/issues/1237)) ([29a98b3](https://github.com/jentic/jentic-one/commit/29a98b303b6070e68dfcfeb2235c4e5dc55cfda8))


### Bug Fixes

* **auth:** accept RFC 8252 private-use redirect schemes on the anonymous DCR door ([#1246](https://github.com/jentic/jentic-one/issues/1246)) ([5e243ce](https://github.com/jentic/jentic-one/commit/5e243ce4ff8f1fcab481c419bc561a1f6bcea26c))
* **auth:** DCR dedupe re-attach honors the D7 client gate ([#1313](https://github.com/jentic/jentic-one/issues/1313)) ([2f9fd5b](https://github.com/jentic/jentic-one/commit/2f9fd5bed5e2b79f82d680d9e70d570884ac1724))
* **auth:** dedupe software_id-less DCR registrations by name + redirect set ([#1261](https://github.com/jentic/jentic-one/issues/1261)) ([5917ed6](https://github.com/jentic/jentic-one/commit/5917ed69ab23c7c75291cd2266eaac329f1fe48e))
* **auth:** include the RFC 6749 §5.1 scope member in every token response ([#1262](https://github.com/jentic/jentic-one/issues/1262)) ([749cbc4](https://github.com/jentic/jentic-one/commit/749cbc45143c4f60a2f0efb17cc3139ea539a922))
* **auth:** omit unset optional members from OAuth responses ([#1259](https://github.com/jentic/jentic-one/issues/1259)) ([25f10b7](https://github.com/jentic/jentic-one/commit/25f10b70a9250bdd26e1000137dd52fcbbf8ab78))
* **auth:** omit unset optional metadata from the anonymous DCR response ([#1250](https://github.com/jentic/jentic-one/issues/1250)) ([749fa18](https://github.com/jentic/jentic-one/commit/749fa1854fba417b07e10531c4c45f246194ed8d))
* **config:** remove static placeholder secret defaults from the shipped image ([#1255](https://github.com/jentic/jentic-one/issues/1255)) ([305f4e2](https://github.com/jentic/jentic-one/commit/305f4e2c8b23d6585e79362495ebe2a4b38a0fd1))
* **deploy:** mount app-secrets on admin/registry and share the parts-mode jwt_secret ([#1258](https://github.com/jentic/jentic-one/issues/1258)) ([b485fb6](https://github.com/jentic/jentic-one/commit/b485fb616d50efdbcc8505622202be362c856bd9))
* **mcp:** answer 405, not the 401 challenge, to credential-less GET /mcp ([#1257](https://github.com/jentic/jentic-one/issues/1257)) ([464de56](https://github.com/jentic/jentic-one/commit/464de56ac4b45cdc89500fe7376bbd44c65c5d80)), closes [#1256](https://github.com/jentic/jentic-one/issues/1256)


### Refactors

* **admin:** factor credential check out of AuthService.login into authenticate ([#1282](https://github.com/jentic/jentic-one/issues/1282)) ([38f4cb9](https://github.com/jentic/jentic-one/commit/38f4cb97ba2fbcf7db6e12cfd5384cf8ff87f803))


### Documentation

* **auth:** document Ed25519 key requirement in RegisterRequest schema ([#566](https://github.com/jentic/jentic-one/issues/566)) ([5817943](https://github.com/jentic/jentic-one/commit/5817943a713d8b24ac4ab5a481baf9583d725b6b))
* **comments:** remove how-it-used-to-be context from code comments ([#1248](https://github.com/jentic/jentic-one/issues/1248)) ([2096b7c](https://github.com/jentic/jentic-one/commit/2096b7cdb345e435503c77fada4eee1ad81ec876))


### Build System

* **deps-dev:** bump @testing-library/react in /ui in the testing group ([#1269](https://github.com/jentic/jentic-one/issues/1269)) ([8fbd113](https://github.com/jentic/jentic-one/commit/8fbd1136aa3114f5374c3827f275382ce5ca781a))
* **deps-dev:** bump @testing-library/user-event ([#1291](https://github.com/jentic/jentic-one/issues/1291)) ([3d3d5cd](https://github.com/jentic/jentic-one/commit/3d3d5cdcc49a5cd6bd8212e001d38f794caf457c))
* **deps-dev:** bump @types/node ([#1294](https://github.com/jentic/jentic-one/issues/1294)) ([ce15862](https://github.com/jentic/jentic-one/commit/ce158627baa4fa6f57ed6a6f68de38bdd5bc1338))
* **deps-dev:** bump @types/node in /ui in the types group ([#1270](https://github.com/jentic/jentic-one/issues/1270)) ([7451e1c](https://github.com/jentic/jentic-one/commit/7451e1c053c08322c6fb1d57b68ff104eb33f4bd))
* **deps-dev:** bump @types/react-dom in /ui in the react group ([#1290](https://github.com/jentic/jentic-one/issues/1290)) ([14a2d76](https://github.com/jentic/jentic-one/commit/14a2d768f31188cf9f075735af3bf0565f9d74ef))
* **deps-dev:** bump @vitejs/plugin-react in /ui in the vite group ([#1265](https://github.com/jentic/jentic-one/issues/1265)) ([403128e](https://github.com/jentic/jentic-one/commit/403128e583da5ff18cbb0e34d79a40f216575278))
* **deps-dev:** bump globals from 17.11.0 to 17.12.0 in /ui ([#1272](https://github.com/jentic/jentic-one/issues/1272)) ([418dde7](https://github.com/jentic/jentic-one/commit/418dde7e7113e472740c310b5999c5f5e4ea16eb))
* **deps-dev:** bump ruff from 0.16.5 to 0.16.6 in the python group ([#1292](https://github.com/jentic/jentic-one/issues/1292)) ([3d88796](https://github.com/jentic/jentic-one/commit/3d88796bd578789e4310d4ddcad156cdfa169510))
* **deps-dev:** bump sharp from 0.35.3 to 0.35.4 in /ui ([#1274](https://github.com/jentic/jentic-one/issues/1274)) ([124d32a](https://github.com/jentic/jentic-one/commit/124d32acf1cc1c007d5253642f716d5f7addaaeb))
* **deps-dev:** bump the vite group across 1 directory with 4 updates ([#1289](https://github.com/jentic/jentic-one/issues/1289)) ([62ea866](https://github.com/jentic/jentic-one/commit/62ea866b244070e1be6e822be4e367f378104112))
* **deps-dev:** bump typescript-eslint ([#1288](https://github.com/jentic/jentic-one/issues/1288)) ([eee0585](https://github.com/jentic/jentic-one/commit/eee05850f5626909150b3f8a4abb5fb70e270de2))
* **deps:** bump @tanstack/react-query from 5.102.3 to 5.102.8 in /ui ([#1273](https://github.com/jentic/jentic-one/issues/1273)) ([04ffce1](https://github.com/jentic/jentic-one/commit/04ffce12e5dc2824a4aede0ea5ac5b2fa4f6099c))
* **deps:** bump framer-motion from 13.1.1 to 13.2.0 in /ui ([#1293](https://github.com/jentic/jentic-one/issues/1293)) ([dd4f04a](https://github.com/jentic/jentic-one/commit/dd4f04a49526c3317756e08fc76697a8e27dbc80))
* **deps:** bump lucide-react from 1.32.0 to 1.39.0 in /ui ([#1271](https://github.com/jentic/jentic-one/issues/1271)) ([2333432](https://github.com/jentic/jentic-one/commit/2333432a2b9919775ea5726910fd62c8aac2a5e2))
* **deps:** bump lucide-react from 1.39.0 to 1.40.0 in /ui ([#1295](https://github.com/jentic/jentic-one/issues/1295)) ([24d05cd](https://github.com/jentic/jentic-one/commit/24d05cdb261b0a58d9c79d3c467649c24bde050d))
* **deps:** bump react-router in /ui in the react group ([#1268](https://github.com/jentic/jentic-one/issues/1268)) ([a0417d2](https://github.com/jentic/jentic-one/commit/a0417d269116568689bbfc524485630e776c396a))
* **deps:** bump the python group with 5 updates ([#1275](https://github.com/jentic/jentic-one/issues/1275)) ([2e27ff7](https://github.com/jentic/jentic-one/commit/2e27ff77edcd55d4564df793446f69614ba377ba))

## [0.38.0](https://github.com/jentic/jentic-one/compare/v0.37.4...v0.38.0) (2026-09-03)


### Features

* **auth:** /mcp-scoped RFC 8414 + RFC 9728 discovery + 401 resource_metadata (3a-4) ([#1221](https://github.com/jentic/jentic-one/issues/1221)) ([fef9aa4](https://github.com/jentic/jentic-one/commit/fef9aa428d39f475119f6b07f038a6c5d75985da))
* **auth:** anonymous DCR front door + awaiting-approval page (3a-2) ([#1219](https://github.com/jentic/jentic-one/issues/1219)) ([7b83f69](https://github.com/jentic/jentic-one/commit/7b83f69d8a882aad5421becaf06bd9796853e4d6))
* **auth:** consent→agent binding with oauth_client_grants + grant-channel tokens (3a-3) ([#1220](https://github.com/jentic/jentic-one/issues/1220)) ([76bdacf](https://github.com/jentic/jentic-one/commit/76bdacf1b1404b4956a7779022a65f67ea525e6c))
* **auth:** OAuth client registry, agent JWKS PUT, and token provenance ([#1151](https://github.com/jentic/jentic-one/issues/1151)) ([288978e](https://github.com/jentic/jentic-one/commit/288978e5cd77127f5435c245cbd334fd54aeffd3))
* **auth:** public secret-less OAuth clients + approval lifecycle (3a-1) ([#1218](https://github.com/jentic/jentic-one/issues/1218)) ([6a3604e](https://github.com/jentic/jentic-one/commit/6a3604efd13487bfd2a170564f3b11c4eae74d27))
* **cli:** add execute + get_execution_result MCP tools with broker hardening ([#1186](https://github.com/jentic/jentic-one/issues/1186)) ([30fbc9c](https://github.com/jentic/jentic-one/commit/30fbc9cfbd37f99d9a1a1c4453b5ae4f55802f8b))
* **cli:** add jentic mcp stdio server skeleton with pre-auth tools ([#1209](https://github.com/jentic/jentic-one/issues/1209)) ([d3f9bc4](https://github.com/jentic/jentic-one/commit/d3f9bc4c1014ebe7069e75319b9c96552ae9f9d6))
* **cli:** add search_apis + inspect_operation MCP discovery tools ([#1183](https://github.com/jentic/jentic-one/issues/1183)) ([2602274](https://github.com/jentic/jentic-one/commit/2602274f9a3ce6d23d1d64e4e3f48b626d1c4012))
* **cli:** add search_catalog + import_api + request_access MCP tools ([#1213](https://github.com/jentic/jentic-one/issues/1213)) ([70b91b2](https://github.com/jentic/jentic-one/commit/70b91b25157596b9ba2cb3da8a540aaa85ae1c5a))
* **cli:** auto-register MCP entries per runtime with optional isolation ([#1191](https://github.com/jentic/jentic-one/issues/1191)) ([a3a6ea5](https://github.com/jentic/jentic-one/commit/a3a6ea5cd8d7766a15efa896081b6f61c3c45fda))
* **cli:** official CLI container image + MCP registry entry (2-E4 PR A+B) ([#1224](https://github.com/jentic/jentic-one/issues/1224)) ([1f203d0](https://github.com/jentic/jentic-one/commit/1f203d0963bdb77d7338927326fe6133faa9757a))
* **cli:** serve skills as MCP resources with hosted/bundled provenance ([#1192](https://github.com/jentic/jentic-one/issues/1192)) ([9ab637c](https://github.com/jentic/jentic-one/commit/9ab637ccea22fb49938919ccec50af8430b780a3))
* **mcp:** daemon-native Streamable HTTP /mcp mount (phase-3 items 1-3) ([#1230](https://github.com/jentic/jentic-one/issues/1230)) ([9856c3f](https://github.com/jentic/jentic-one/commit/9856c3ff90a9bae5415637a0045f7956e35ba9c9))
* **mcp:** HTTP-transport session telemetry + docs/advertisement (phase-3 items 6+8) ([#1232](https://github.com/jentic/jentic-one/issues/1232)) ([d262d6f](https://github.com/jentic/jentic-one/commit/d262d6f4e51938c6b679d45b01787e9f0e8bea5a))
* **mcp:** isolated local daemon mode — jentic mcp --http + --connect relay (phase-3 item 9) ([#1234](https://github.com/jentic/jentic-one/issues/1234)) ([250ff66](https://github.com/jentic/jentic-one/commit/250ff66b56b06564f09dd05ac650b2be56c6b615))
* **telemetry:** add Origin.MCP + MCP session/config events ([#1178](https://github.com/jentic/jentic-one/issues/1178)) ([241c609](https://github.com/jentic/jentic-one/commit/241c6094feac114a82117a06e9f56f89e0bb10da))
* **ui:** OAuth approval queue + per-agent connected-clients panel (3a-5) ([#1223](https://github.com/jentic/jentic-one/issues/1223)) ([5c83bfa](https://github.com/jentic/jentic-one/commit/5c83bfaa58201adf0e0900b99e008fc4fa4e57b1))
* **ui:** per-agent MCP config card + sessions list + origin filter ([#1210](https://github.com/jentic/jentic-one/issues/1210)) ([d6e9eb5](https://github.com/jentic/jentic-one/commit/d6e9eb5e7d8103b1b7b8e06e22ef34ac9d7bc65e))


### Bug Fixes

* **auth:** revoke OAuth client grants on agent ownership transfer (G10, [#1222](https://github.com/jentic/jentic-one/issues/1222)) ([#1231](https://github.com/jentic/jentic-one/issues/1231)) ([5737f99](https://github.com/jentic/jentic-one/commit/5737f9991f77770d356764cb4de018927d464b9c))
* **cli:** pin CA on cobra execute and refuse redirects on the broker leg ([#1217](https://github.com/jentic/jentic-one/issues/1217)) ([25ec336](https://github.com/jentic/jentic-one/commit/25ec336a4d381b346eae62a8c49550bf5a667d71))
* **cli:** route token mint through pinned CA transport + attribution hook (closes [#1205](https://github.com/jentic/jentic-one/issues/1205)) ([#1215](https://github.com/jentic/jentic-one/issues/1215)) ([84e866a](https://github.com/jentic/jentic-one/commit/84e866a400da73f948fab91d2f605b8c71550283))
* **db:** linearize admin migration heads after parallel merges ([#1211](https://github.com/jentic/jentic-one/issues/1211)) ([298b24f](https://github.com/jentic/jentic-one/commit/298b24faa7f8f16ac73abc71bc008e16e22128a5))
* **ui:** use --url in the DCR quickstart register snippet (closes [#1204](https://github.com/jentic/jentic-one/issues/1204)) ([#1216](https://github.com/jentic/jentic-one/issues/1216)) ([c555e2e](https://github.com/jentic/jentic-one/commit/c555e2e10194ffcdf2f1e1baa3408d29c6d5a91e))


### Refactors

* **cli:** extract agentops from cmd for reuse ([#1179](https://github.com/jentic/jentic-one/issues/1179)) ([0923f06](https://github.com/jentic/jentic-one/commit/0923f065a680b9994eb683e8b6638c1ac6dd8b03))


### Documentation

* **deploy:** buyer-facing AWS Marketplace install guide ([#1187](https://github.com/jentic/jentic-one/issues/1187)) ([8fa0e5c](https://github.com/jentic/jentic-one/commit/8fa0e5c134a4427db77a05245fdb5ca2cef9a5cf))
* **web:** advertise jentic mcp in llms.txt + same-host hardening recipes ([#1180](https://github.com/jentic/jentic-one/issues/1180)) ([3955d5f](https://github.com/jentic/jentic-one/commit/3955d5f0c5eddf324f33851760a972998a658487))


### Build System

* **deps-dev:** bump @testing-library/user-event ([#1197](https://github.com/jentic/jentic-one/issues/1197)) ([1e58ced](https://github.com/jentic/jentic-one/commit/1e58cedf9a1aa9889ee5c3169301e456f391e0a5))
* **deps-dev:** bump @types/node ([#1198](https://github.com/jentic/jentic-one/issues/1198)) ([e70094f](https://github.com/jentic/jentic-one/commit/e70094f694b13a8450cf5114ea5cdd2f804fb1eb))
* **deps-dev:** bump @types/react-dom ([#1196](https://github.com/jentic/jentic-one/issues/1196)) ([ed78328](https://github.com/jentic/jentic-one/commit/ed783286bcce279e88281811fb4f61c5fd0260c0))
* **deps-dev:** bump browserslist from 4.28.2 to 4.28.8 in /ui ([#1226](https://github.com/jentic/jentic-one/issues/1226)) ([a856092](https://github.com/jentic/jentic-one/commit/a856092849e70f21b4ba16c93e92682385f9d12e))
* **deps-dev:** bump the python group with 3 updates ([#1202](https://github.com/jentic/jentic-one/issues/1202)) ([6cd37e1](https://github.com/jentic/jentic-one/commit/6cd37e1d5e584c076ab0399bf8ecac0e82f84228))
* **deps-dev:** bump the vite group in /ui with 2 updates ([#1193](https://github.com/jentic/jentic-one/issues/1193)) ([428fe17](https://github.com/jentic/jentic-one/commit/428fe1705653406b01825af858c5ae7cfedddd5a))
* **deps:** bump @tanstack/react-query from 5.101.4 to 5.102.3 in /ui ([#1199](https://github.com/jentic/jentic-one/issues/1199)) ([31133e1](https://github.com/jentic/jentic-one/commit/31133e16954d5499029f59641505ae8680d606dc))
* **deps:** bump framer-motion from 13.1.0 to 13.1.1 in /ui ([#1200](https://github.com/jentic/jentic-one/issues/1200)) ([bcf0c34](https://github.com/jentic/jentic-one/commit/bcf0c34bc69b6202aeaa45d9881051704c4e989a))

## [0.37.4](https://github.com/jentic/jentic-one/compare/v0.37.3...v0.37.4) (2026-08-28)


### Bug Fixes

* **deploy:** set JENTIC__APPS=broker on the Marketplace broker pod ([#1182](https://github.com/jentic/jentic-one/issues/1182)) ([6d2c883](https://github.com/jentic/jentic-one/commit/6d2c883da9a639263c09046c80fae598fefbf58e))
* **entitlement:** use Count entitlements and release the checkout seat ([#1184](https://github.com/jentic/jentic-one/issues/1184)) ([a451813](https://github.com/jentic/jentic-one/commit/a4518138997b97db3f7b6df9be19dcbf18b69eab))

## [0.37.3](https://github.com/jentic/jentic-one/compare/v0.37.2...v0.37.3) (2026-08-28)


### Bug Fixes

* **docker:** venv-install the wheel in the per-service images too ([#1172](https://github.com/jentic/jentic-one/issues/1172)) ([c2f6669](https://github.com/jentic/jentic-one/commit/c2f6669c2b95bc7dc22fc6b15e06bea1bbe2c48d))

## [0.37.2](https://github.com/jentic/jentic-one/compare/v0.37.1...v0.37.2) (2026-08-28)


### Bug Fixes

* **docker:** move shipped images off Debian's won't-fix glibc CVEs ([#1170](https://github.com/jentic/jentic-one/issues/1170)) ([a0bfee7](https://github.com/jentic/jentic-one/commit/a0bfee76feed842bb64ece4b67f7f37886521062))

## [0.37.1](https://github.com/jentic/jentic-one/compare/v0.37.0...v0.37.1) (2026-08-27)


### Bug Fixes

* **ci:** drop stale placeholder assertion from the Marketplace chart gate ([#1166](https://github.com/jentic/jentic-one/issues/1166)) ([a0c363f](https://github.com/jentic/jentic-one/commit/a0c363f9ea5695c765271f6de8e8e99a829dd87d))
* **helm:** bake un-prefixed image tags into the Marketplace chart ([#1169](https://github.com/jentic/jentic-one/issues/1169)) ([67fb464](https://github.com/jentic/jentic-one/commit/67fb4647953430c5386f6435a5e2d9ec0c13fc8b))

## [0.37.0](https://github.com/jentic/jentic-one/compare/v0.36.0...v0.37.0) (2026-08-27)


### Features

* **helm:** generate the bundled-DB passwords too — zero-touch Marketplace install ([#1163](https://github.com/jentic/jentic-one/issues/1163)) ([bece9f0](https://github.com/jentic/jentic-one/commit/bece9f0e2649745a147211eac2ee27fdc1990cab))


### Bug Fixes

* **helm:** stamp explicit per-service image tags into the baked Marketplace chart ([#1164](https://github.com/jentic/jentic-one/issues/1164)) ([5e4ab87](https://github.com/jentic/jentic-one/commit/5e4ab87e7f63f9425848033f14874406a2ab8594))

## [0.36.0](https://github.com/jentic/jentic-one/compare/v0.35.0...v0.36.0) (2026-08-27)


### Features

* **helm:** generate all mandatory app secrets, not just the keyset ([#1160](https://github.com/jentic/jentic-one/issues/1160)) ([aaf72ec](https://github.com/jentic/jentic-one/commit/aaf72ec415f7568991fd1537007fad9eb1c5ef0c))


### Bug Fixes

* **ci:** create the cli/vX.Y.Z tag via the REST API, not git push ([#1162](https://github.com/jentic/jentic-one/issues/1162)) ([7fcedbd](https://github.com/jentic/jentic-one/commit/7fcedbd65f495146e05217d5a9fed17903bdd5e1))

## [0.35.0](https://github.com/jentic/jentic-one/compare/v0.34.0...v0.35.0) (2026-08-27)


### Features

* **helm:** chart-managed credential-encryption keyset (global.encryption) ([#1159](https://github.com/jentic/jentic-one/issues/1159)) ([90dfd00](https://github.com/jentic/jentic-one/commit/90dfd00de5a4cf4d697e53720b67480d1b93c0b1))


### Bug Fixes

* **helm:** AWS-parseable image format for the Marketplace psql reference ([#1156](https://github.com/jentic/jentic-one/issues/1156)) ([a493b2b](https://github.com/jentic/jentic-one/commit/a493b2b4337447fa4dee3b680e9330f9a3b64e03))

## [0.34.0](https://github.com/jentic/jentic-one/compare/v0.33.0...v0.34.0) (2026-08-27)


### Features

* **helm:** pass AWS Marketplace chart validation ([#1155](https://github.com/jentic/jentic-one/issues/1155)) ([7bb2526](https://github.com/jentic/jentic-one/commit/7bb2526e0717b01998bfeb651e86e35fd3c878ba))


### Bug Fixes

* **docker:** harden the Marketplace postgres mirror to pass the Trivy gate ([#1153](https://github.com/jentic/jentic-one/issues/1153)) ([e8d0092](https://github.com/jentic/jentic-one/commit/e8d0092263d6b732bc502f8b61af3dfa24686bbd))

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
