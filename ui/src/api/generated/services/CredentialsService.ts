/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AccountUpdate } from '../models/AccountUpdate';
import type { ConnectLinkRequest } from '../models/ConnectLinkRequest';
import type { CredentialCreate } from '../models/CredentialCreate';
import type { CredentialOut } from '../models/CredentialOut';
import type { CredentialPatch } from '../models/CredentialPatch';
import type { OAuthBrokerCreate } from '../models/OAuthBrokerCreate';
import type { OAuthBrokerOut } from '../models/OAuthBrokerOut';
import type { OAuthBrokerUpdate } from '../models/OAuthBrokerUpdate';
import type { SyncRequest } from '../models/SyncRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class CredentialsService {
    /**
     * Query the persistent audit log
     * Return audit rows newest-first. Drives the credential history panel in the UI.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static queryAuditEventsAuditGet({
        targetKind,
        targetId,
        credentialId,
        event,
        limit = 50,
        offset,
    }: {
        /**
         * Filter by target kind (e.g. 'credential', 'toolkit')
         */
        targetKind?: (string | null),
        /**
         * Filter by target ID
         */
        targetId?: (string | null),
        /**
         * Convenience: equivalent to target_kind=credential&target_id=<this>
         */
        credentialId?: (string | null),
        /**
         * Filter by event name
         */
        event?: (string | null),
        /**
         * Max rows to return
         */
        limit?: number,
        /**
         * Pagination offset
         */
        offset?: number,
    }): CancelablePromise<Array<Record<string, any>>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/audit',
            query: {
                'target_kind': targetKind,
                'target_id': targetId,
                'credential_id': credentialId,
                'event': event,
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Store an upstream API credential — add a secret to the vault for broker injection
     * Store an encrypted credential in the vault for automatic broker injection.
     *
     * Values are encrypted at rest and **never returned** after creation. Set `api_id` to
     * bind the credential to an API; the broker will inject it automatically when proxying
     * calls to that API.
     *
     * ---
     *
     * ### `auth_type` reference
     *
     * Set `auth_type` to tell the broker how to inject the credential into upstream requests.
     * Based on the [Postman auth type taxonomy](https://learning.postman.com/docs/sending-requests/authorization/authorization-types/).
     *
     * | `auth_type` | Status | Broker injects | `value` | `identity` |
     * |---|---|---|---|---|
     * | `bearer` | ✅ implemented | `Authorization: Bearer {value}` | Token, PAT, or OAuth access token | Not used |
     * | `basic` | ✅ implemented | `Authorization: Basic base64({identity or "token"}:{value})` | Password or PAT | Username (optional — defaults to `"token"` if omitted, works for GitHub PATs) |
     * | `apiKey` | ✅ implemented | Custom header or query param `= {value}` | API key | For **compound schemes** (e.g. Discourse `Api-Key` + `Api-Username`): set `identity` to the username — one credential covers both headers when the overlay uses canonical `Secret`/`Identity` scheme names |
     * | `oauth2` | ⚠️ partial | `Authorization: Bearer {value}` — token must be pre-obtained | Access token (Pipedream-managed flows only via `pipedream_oauth`) | Not used |
     * | `digest` | 🔲 planned | RFC 2617 challenge-response (nonce/HMAC handshake) | Password | Username |
     * | `jwt` | 🔲 planned | `Authorization: Bearer {signed_jwt}` — auto-generated from signing key | Private key or secret | Key ID (`kid`) — signing algorithm and claims go in `context` |
     * | `aws_sig4` | 🔲 planned | `Authorization: AWS4-HMAC-SHA256 ...` signed headers | AWS Secret Access Key | AWS Access Key ID — region and service go in `context` |
     * | `oauth1` | 🔲 planned | HMAC-SHA1 signed request (nonce + timestamp) | OAuth secret | OAuth consumer key |
     * | `hawk` | 🔲 planned | `Authorization: Hawk ...` HMAC request signing | Hawk secret | Hawk key ID |
     * | `ntlm` | 🔲 not planned | Windows NTLM challenge-response | Password | Username + domain |
     * | `akamai_edgegrid` | 🔲 not planned | Akamai EdgeGrid signing | Client secret | Client token + access token in `context` |
     *
     * **Notes:**
     * - `pipedream_oauth` is a reserved value written by the Pipedream integration — do not set it manually.
     * - For `oauth2` full flows (auth code, client credentials, PKCE, token refresh) see the roadmap.
     * - `context` (not yet exposed) will hold auxiliary fields for multi-value schemes (JWT claims, AWS region/service, etc.).
     *
     * ---
     *
     * ### Workflow
     *
     * 1. Call `GET /apis/{api_id}` — check `security_schemes` and `credentials_configured` to find gaps.
     * 2. Post this endpoint with `api_id`, `auth_type`, `value` (and `identity` if needed).
     * 3. The broker injects the credential automatically on every proxied call to that API.
     * 4. To scope a credential to a specific toolkit: `POST /toolkits/{id}/credentials`.
     *
     * If the API has no registered security scheme yet, submit an overlay first: `POST /apis/{api_id}/overlays`.
     * @returns CredentialOut Successful Response
     * @throws ApiError
     */
    public static createCredentialsPost({
        requestBody,
    }: {
        /**
         * Credential details: label for identification, encrypted value (API key/token/password), optional identity (username/client ID), API ID, and auth type
         */
        requestBody: CredentialCreate,
    }): CancelablePromise<CredentialOut> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/credentials',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List upstream API credentials — labels and API bindings only, no secret values
     * List stored upstream API credentials. Values are never returned.
     *
     * All authenticated callers (agent keys and human sessions) can see all credential
     * labels and IDs — this is intentional. Labels are not secrets, and agents need
     * to discover credential IDs in order to file targeted `grant` access requests
     * (e.g. "bind Work Gmail" vs "bind Personal Gmail").
     *
     * Use `GET /credentials/{id}` to retrieve a specific credential by ID.
     * Filter with `?api_id=api.github.com` to list all credentials for a given API.
     * @returns CredentialOut Successful Response
     * @throws ApiError
     */
    public static listCredentialsCredentialsGet({
        apiId,
    }: {
        /**
         * Filter credentials by API ID (hostname)
         */
        apiId?: (string | null),
    }): CancelablePromise<Array<CredentialOut>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/credentials',
            query: {
                'api_id': apiId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get an upstream API credential by ID
     * Retrieve metadata for a single credential. Value is never returned.
     * @returns CredentialOut Successful Response
     * @throws ApiError
     */
    public static getCredentialCredentialsCidGet({
        cid,
    }: {
        cid: string,
    }): CancelablePromise<CredentialOut> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/credentials/{cid}',
            path: {
                'cid': cid,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update an upstream API credential — rotate a secret or fix its API binding
     * Update a credential's label, secret value, identity field, API binding, or auth_type.
     *
     * Common use cases:
     * - Rotate an expired token or password (update `value`)
     * - Fix incorrect API binding (update `api_id`)
     * - Add username to existing credential (update `identity`)
     * - Relabel for clarity (update `label`)
     *
     * Only changed fields need to be included in the request body. Omitted fields are left unchanged.
     *
     * **Auth:** Requires human session OR agent key with explicit `PATCH /credentials` allow rule on jentic-mini credential.
     * @returns CredentialOut Successful Response
     * @throws ApiError
     */
    public static patchCredentialsCidPatch({
        cid,
        requestBody,
    }: {
        /**
         * Credential ID to update
         */
        cid: string,
        /**
         * Fields to update: label, value (for rotation), identity, api_id, or auth_type — only provided fields are changed
         */
        requestBody: CredentialPatch,
    }): CancelablePromise<CredentialOut> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/credentials/{cid}',
            path: {
                'cid': cid,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete an upstream API credential
     * Permanently delete a credential.
     *
     * The credential is removed from the vault and unbound from all toolkits that reference it.
     * Agents using toolkits with this credential will immediately lose access to the upstream API.
     *
     * For credentials backed by Pipedream OAuth (`auth_type == 'pipedream_oauth'`), the
     * upstream Pipedream grant is also revoked so the connection cannot be re-used out-of-band.
     * Failures on the upstream revoke are logged but do not block local deletion — local
     * cleanup is the source of truth.
     *
     * **Auth:** Requires human session OR agent key with explicit `DELETE /credentials` allow rule on jentic-mini credential.
     *
     * **Warning:** This operation cannot be undone. The secret value is irrecoverably destroyed.
     * @returns void
     * @throws ApiError
     */
    public static deleteCredentialsCidDelete({
        cid,
    }: {
        /**
         * Credential ID to delete
         */
        cid: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/credentials/{cid}',
            path: {
                'cid': cid,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List toolkits this credential is bound to
     * Return `[{toolkit_id, toolkit_name, alias}]` for every toolkit this credential is bound to.
     *
     * Powers the per-row "Used by N toolkits" chip cluster in the credentials list and the
     * cascade-impact preview in `ConfirmDeleteDialog`'s credential variant. A single indexed
     * JOIN — avoids the N+1 fan-out the UI would otherwise have to do.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listCredentialBindingsCredentialsCidBindingsGet({
        cid,
    }: {
        /**
         * Credential ID
         */
        cid: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/credentials/{cid}/bindings',
            path: {
                'cid': cid,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Test a credential by issuing a low-impact upstream probe
     * Verify a credential by issuing a single 5-second probe to the upstream API.
     *
     * The probe URL is chosen, in priority order:
     * 1. `x-jentic-healthcheck` declared in the API's OpenAPI spec
     * 2. The first `GET` operation in the spec with no required parameters
     * 3. The root URL of the API's first declared server
     * 4. The credential's first declared route host
     *
     * Response shape: `{ ok: bool, status: int | null, hint: string | null, probe_url: string | null }`.
     * A 2xx upstream response is `ok=true`. 401/403 returns `ok=false` with a hint that the
     * credential is rejected. 404/405 on a probe path is treated as `ok=true` since the
     * upstream **did respond** — we only care that the credential is plausibly valid.
     *
     * No body is sent. No agent-policy involvement. Used by the credentials UI's
     * "Test connection" button on the form page and (later) inline next to the
     * credential row in the list.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static testCredentialCredentialsCidTestPost({
        cid,
    }: {
        /**
         * Credential ID to test
         */
        cid: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/credentials/{cid}/test',
            path: {
                'cid': cid,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List registered OAuth brokers
     * Return all registered OAuth brokers as a flat list. `client_secret` is never included.
     *
     * Accessible to both agents (toolkit key) and humans (session).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listOauthBrokersOauthBrokersGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/oauth-brokers',
        });
    }
    /**
     * Register an OAuth broker
     * Register a delegated OAuth broker. Currently supported type: `pipedream`.
     *
     * ---
     *
     * ### Pipedream — one-time setup
     *
     * Before registering, complete these steps in the Pipedream UI:
     *
     * **1.** Go to [pipedream.com](https://pipedream.com) and sign in or create an account.
     *
     * **2.** Go to **Settings** (main menu) → **API** → click **+ New OAuth Client**.
     * Name it "Jentic". Store the **client ID** and **client secret** safely — the secret is not shown again.
     *
     * **3.** Go to **Projects** (main menu) and click **+ New Project**. Name it "Jentic".
     *
     * **4.** Go to **Projects → Jentic → Settings** and note the **project ID** (format: `proj_xxx`).
     *
     * That's it. Register the broker below — Jentic automatically configures the Connect
     * application name, support email, and logo in Pipedream on your behalf, so you don't
     * need to touch the Connect → Configuration screen manually.
     *
     * ---
     *
     * ### Registration
     *
     * ```json
     * {
         * "type": "pipedream",
         * "config": {
             * "client_id": "oa_abc123",
             * "client_secret": "pd_secret_xxxx",
             * "project_id": "proj_abc123",
             * "support_email": "support@example.com"
             * }
             * }
             * ```
             *
             * `support_email` is optional but recommended — it is displayed to end users in the
             * Pipedream OAuth consent UI.
             *
             * `client_secret` is write-only — Fernet-encrypted at rest, never returned.
             *
             * ---
             *
             * ### After registration
             *
             * Once registered, connect individual apps with `POST /oauth-brokers/{id}/connect-link`
             * (pass `app` as the Pipedream app slug, e.g. `gmail`, `google_calendar`, `slack`).
             * After the user completes OAuth, call `POST /oauth-brokers/{id}/sync` to pull the
             * connected account into Jentic. From that point, requests to that API's host are
             * automatically proxied with the user's OAuth token injected server-side.
             * @returns OAuthBrokerOut Successful Response
             * @throws ApiError
             */
            public static createOauthBrokerOauthBrokersPost({
                requestBody,
            }: {
                /**
                 * Broker configuration: type (e.g. 'pipedream'), provider-specific config, and encrypted credentials
                 */
                requestBody: OAuthBrokerCreate,
            }): CancelablePromise<OAuthBrokerOut> {
                return __request(OpenAPI, {
                    method: 'POST',
                    url: '/oauth-brokers',
                    body: requestBody,
                    mediaType: 'application/json',
                    errors: {
                        422: `Validation Error`,
                    },
                });
            }
            /**
             * Update an OAuth broker configuration
             * Update client_id, client_secret, and/or project_id for an existing broker.
             *
             * Only supplied fields are changed. client_secret is re-encrypted if provided.
             * @returns OAuthBrokerOut Successful Response
             * @throws ApiError
             */
            public static updateOauthBrokerOauthBrokersBrokerIdPatch({
                brokerId,
                requestBody,
            }: {
                /**
                 * The broker ID
                 */
                brokerId: string,
                /**
                 * Provider-specific config fields to update: client_id, client_secret, project_id — only provided fields are changed, secrets re-encrypted
                 */
                requestBody: OAuthBrokerUpdate,
            }): CancelablePromise<OAuthBrokerOut> {
                return __request(OpenAPI, {
                    method: 'PATCH',
                    url: '/oauth-brokers/{broker_id}',
                    path: {
                        'broker_id': brokerId,
                    },
                    body: requestBody,
                    mediaType: 'application/json',
                    errors: {
                        422: `Validation Error`,
                    },
                });
            }
            /**
             * Get an OAuth broker
             * Retrieve OAuth broker configuration and metadata.
             *
             * Returns broker type, client ID, project ID, and connected account statistics.
             * Use this to verify a broker is registered before creating connect links or syncing accounts.
             *
             * For connected account details, use `GET /oauth-brokers/{broker_id}/accounts`.
             * @returns any Successful Response
             * @throws ApiError
             */
            public static getOauthBrokerOauthBrokersBrokerIdGet({
                brokerId,
            }: {
                /**
                 * The broker ID
                 */
                brokerId: string,
            }): CancelablePromise<any> {
                return __request(OpenAPI, {
                    method: 'GET',
                    url: '/oauth-brokers/{broker_id}',
                    path: {
                        'broker_id': brokerId,
                    },
                    errors: {
                        422: `Validation Error`,
                    },
                });
            }
            /**
             * Remove an OAuth broker
             * Remove a broker and all its connected accounts and credentials.
             *
             * Cascades through oauth_broker_accounts -> toolkit_credentials -> vault.
             * Does not revoke tokens on the provider side - do that in the provider's dashboard.
             * @returns any Successful Response
             * @throws ApiError
             */
            public static deleteOauthBrokerOauthBrokersBrokerIdDelete({
                brokerId,
            }: {
                /**
                 * The broker ID
                 */
                brokerId: string,
            }): CancelablePromise<any> {
                return __request(OpenAPI, {
                    method: 'DELETE',
                    url: '/oauth-brokers/{broker_id}',
                    path: {
                        'broker_id': brokerId,
                    },
                    errors: {
                        422: `Validation Error`,
                    },
                });
            }
            /**
             * List connected accounts for an OAuth broker
             * List the OAuth-connected account mappings stored for this broker.
             *
             * Each entry represents a SaaS app the user has connected via Pipedream's OAuth
             * UI, along with the API host it maps to and the Pipedream `account_id` used when
             * routing requests through the proxy.
             *
             * Use `POST /oauth-brokers/{id}/sync` to refresh this list from Pipedream.
             * @returns any Successful Response
             * @throws ApiError
             */
            public static listBrokerAccountsOauthBrokersBrokerIdAccountsGet({
                brokerId,
                externalUserId,
            }: {
                /**
                 * The broker ID
                 */
                brokerId: string,
                /**
                 * Filter by external user ID
                 */
                externalUserId?: (string | null),
            }): CancelablePromise<any> {
                return __request(OpenAPI, {
                    method: 'GET',
                    url: '/oauth-brokers/{broker_id}/accounts',
                    path: {
                        'broker_id': brokerId,
                    },
                    query: {
                        'external_user_id': externalUserId,
                    },
                    errors: {
                        422: `Validation Error`,
                    },
                });
            }
            /**
             * Generate a Pipedream Connect Link for authorising apps
             * Generate a short-lived Pipedream Connect Link URL.
             *
             * Visit the returned `connect_link_url` in a browser to authorise SaaS apps
             * (e.g. Gmail, Slack, GitHub) via Pipedream's hosted OAuth consent UI.
             *
             * After completing the OAuth flow, call `POST /oauth-brokers/{id}/sync` to
             * pull the new account into jentic-mini so requests start routing through it.
             *
             * The link expires after ~1 hour. Generate a new one if it expires before use.
             *
             * Intentionally open to agents (not human-session-only): only a human can
             * complete the OAuth flow, so generating the link is safe for agents to initiate.
             * Requires at minimum a valid toolkit key or trusted-subnet (admin) access.
             * @returns any Successful Response
             * @throws ApiError
             */
            public static createConnectLinkOauthBrokersBrokerIdConnectLinkPost({
                brokerId,
                requestBody,
            }: {
                /**
                 * The broker ID
                 */
                brokerId: string,
                /**
                 * Connect link request: Pipedream app slug (e.g. gmail, slack), human-readable label for the connection, and optional api_id override for catalog binding
                 */
                requestBody: ConnectLinkRequest,
            }): CancelablePromise<any> {
                return __request(OpenAPI, {
                    method: 'POST',
                    url: '/oauth-brokers/{broker_id}/connect-link',
                    path: {
                        'broker_id': brokerId,
                    },
                    body: requestBody,
                    mediaType: 'application/json',
                    errors: {
                        422: `Validation Error`,
                    },
                });
            }
            /**
             * Sync connected accounts from the OAuth broker
             * Re-fetch connected accounts from the provider and update local mappings.
             *
             * Call this after connecting a new app via Pipedream's hosted OAuth UI —
             * the new account will appear in subsequent `GET /oauth-brokers/{id}/accounts`
             * responses and the broker will start routing requests to it automatically.
             *
             * This does **not** affect accounts already connected — it is additive.
             *
             * Intentionally open to agents: syncing pulls in credentials the human already
             * authorised. No new OAuth flows are initiated.
             * @returns any Successful Response
             * @throws ApiError
             */
            public static syncBrokerAccountsOauthBrokersBrokerIdSyncPost({
                brokerId,
                requestBody,
            }: {
                /**
                 * The broker ID
                 */
                brokerId: string,
                /**
                 * Sync request: list of API slugs to sync accounts for (fetches connected accounts from broker and imports as Jentic credentials)
                 */
                requestBody: SyncRequest,
            }): CancelablePromise<any> {
                return __request(OpenAPI, {
                    method: 'POST',
                    url: '/oauth-brokers/{broker_id}/sync',
                    path: {
                        'broker_id': brokerId,
                    },
                    body: requestBody,
                    mediaType: 'application/json',
                    errors: {
                        422: `Validation Error`,
                    },
                });
            }
            /**
             * Remove a connected account from an OAuth broker
             * Remove a specific connected account from this broker.
             *
             * This performs three actions in order:
             * 1. Revokes the account in the upstream provider (Pipedream) via their API
             * 2. Removes the associated credential from all toolkit provisioning
             * 3. Deletes the credential from the vault and the account from the local DB
             *
             * If the Pipedream revoke fails, the local cleanup still proceeds (with a warning).
             * @returns any Successful Response
             * @throws ApiError
             */
            public static deleteBrokerAccountOauthBrokersBrokerIdAccountsAccountIdDelete({
                brokerId,
                accountId,
            }: {
                /**
                 * The broker ID
                 */
                brokerId: string,
                /**
                 * Connected account ID to delete
                 */
                accountId: string,
            }): CancelablePromise<any> {
                return __request(OpenAPI, {
                    method: 'DELETE',
                    url: '/oauth-brokers/{broker_id}/accounts/{account_id}',
                    path: {
                        'broker_id': brokerId,
                        'account_id': accountId,
                    },
                    errors: {
                        422: `Validation Error`,
                    },
                });
            }
            /**
             * Update a connected account (e.g. rename label)
             * Patch a connected account record.
             *
             * Updates the display label for a connected OAuth account. The account remains linked
             * to the same external OAuth identity and credentials are not affected. Label changes
             * are reflected in both the oauth_broker_accounts table and any associated credentials
             * in the vault.
             *
             * Parameters:
             * broker_id: OAuth broker ID (e.g. 'pipedream')
             * account_id: Connected account ID from the broker
             * body: Update request containing the new label
             *
             * Returns:
             * Updated account_id and label.
             *
             * Auth: Requires human session (admin only).
             *
             * Currently supports updating label only. Future versions may support updating
             * additional account metadata.
             * @returns any Successful Response
             * @throws ApiError
             */
            public static updateBrokerAccountOauthBrokersBrokerIdAccountsAccountIdPatch({
                brokerId,
                accountId,
                requestBody,
            }: {
                /**
                 * The broker ID
                 */
                brokerId: string,
                /**
                 * Connected account ID to update
                 */
                accountId: string,
                /**
                 * Account update: new display label for this connected OAuth account
                 */
                requestBody: AccountUpdate,
            }): CancelablePromise<any> {
                return __request(OpenAPI, {
                    method: 'PATCH',
                    url: '/oauth-brokers/{broker_id}/accounts/{account_id}',
                    path: {
                        'broker_id': brokerId,
                        'account_id': accountId,
                    },
                    body: requestBody,
                    mediaType: 'application/json',
                    errors: {
                        422: `Validation Error`,
                    },
                });
            }
            /**
             * Get a reconnect link for an existing connected account
             * Generate a new OAuth connect link for an existing connected account.
             *
             * The returned URL sends the user through the Pipedream OAuth flow for the
             * same app slug.  On completion, the callback will:
             *
             * 1. Sync the broker (discovering the new account).
             * 2. If the new account is confirmed present, delete the old account.
             *
             * This allows a user to re-authorise a broken connection without losing the
             * existing credential until the replacement is confirmed.
             * @returns any Successful Response
             * @throws ApiError
             */
            public static reconnectAccountLinkOauthBrokersBrokerIdAccountsAccountIdReconnectLinkPost({
                brokerId,
                accountId,
            }: {
                /**
                 * The broker ID
                 */
                brokerId: string,
                /**
                 * OAuth broker account ID to reconnect
                 */
                accountId: string,
            }): CancelablePromise<any> {
                return __request(OpenAPI, {
                    method: 'POST',
                    url: '/oauth-brokers/{broker_id}/accounts/{account_id}/reconnect-link',
                    path: {
                        'broker_id': brokerId,
                        'account_id': accountId,
                    },
                    errors: {
                        422: `Validation Error`,
                    },
                });
            }
        }
