/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentCreateRequest } from '../models/AgentCreateRequest';
import type { AgentListResponse } from '../models/AgentListResponse';
import type { AgentPatchRequest } from '../models/AgentPatchRequest';
import type { AgentPermissionsRequest } from '../models/AgentPermissionsRequest';
import type { AgentPermissionsResponse } from '../models/AgentPermissionsResponse';
import type { AgentResponse } from '../models/AgentResponse';
import type { ApiKeyHistoryResponse } from '../models/ApiKeyHistoryResponse';
import type { ApiKeyInfoResponse } from '../models/ApiKeyInfoResponse';
import type { ApiKeyResponse } from '../models/ApiKeyResponse';
import type { ClaimRequest } from '../models/ClaimRequest';
import type { CredentialBindingListResponse } from '../models/CredentialBindingListResponse';
import type { CredentialBindingResponse } from '../models/CredentialBindingResponse';
import type { CredentialBindRequest } from '../models/CredentialBindRequest';
import type { DenyRequest } from '../models/DenyRequest';
import type { JwksUpdateRequest } from '../models/JwksUpdateRequest';
import type { OAuthGrantListResponse } from '../models/OAuthGrantListResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AgentsService {
    /**
     * List visible agents
     * List the agents the caller can see, newest first.
     *
     * Visibility is scoped to the caller: `org:admin` sees every agent; anyone
     * else sees the agents they own. An agent caller also sees itself, and with
     * `owner:agents:read` its owner's agents. Filter by lifecycle `status`
     * (`pending`, `active`, `rejected`, `disabled`, `archived`). Results are
     * cursor-paginated: pass `next_cursor` from the previous page as `cursor`
     * while `has_more` is true.
     * @returns AgentListResponse Successful Response
     * @throws ApiError
     */
    public static listAgents({
        cursor,
        limit = 50,
        status,
    }: {
        /**
         * Cursor from a previous page's `next_cursor`; omit for the first page.
         */
        cursor?: (string | null),
        /**
         * Maximum number of agents to return (1-200, default 50).
         */
        limit?: number,
        /**
         * Only return agents in this lifecycle status: `pending`, `active`, `rejected`, `disabled` or `archived`.
         */
        status?: (string | null),
    }): CancelablePromise<AgentListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/agents',
            query: {
                'cursor': cursor,
                'limit': limit,
                'status': status,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Create an agent
     * Create an agent owned by the caller.
     *
     * The agent is `active` immediately and owned by the caller. Supply `name`
     * (required), an optional `description`, and optional `permissions`. When
     * `permissions` is omitted the agent gets the default agent permission set.
     * When supplied, every permission must be one the caller may grant (see
     * `grantable_by_caller` on `GET /permissions`); otherwise the request fails
     * with 403 (`agent_permission_not_grantable`). Returns the created agent.
     *
     * The agent has no way to authenticate yet. Follow up with
     * `POST /agents/{agent_id}:generate-api-key` (or `PUT /agents/{agent_id}/jwks`
     * for JWT-bearer), and bind credentials with `POST /agents/{agent_id}/credentials`.
     * @returns AgentResponse Successful Response
     * @throws ApiError
     */
    public static createAgent({
        requestBody,
    }: {
        /**
         * Request body for creating an agent manually.
         */
        requestBody: AgentCreateRequest,
    }): CancelablePromise<AgentResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agents',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Archive Agent
     * Archive an agent — or, with ``purge=true``, hard-delete it.
     *
     * Archive keeps the row for history, but the action is not reversible and
     * the agent's authority is swept: permission grants, credential bindings, and
     * OAuth consent grants are revoked. For the reversible kill switch use
     * ``:disable`` / ``:enable`` instead.
     *
     * Purge removes the agent and its authority and session rows; it archives
     * first if needed, so the agent's authority is gone before any cleanup runs,
     * and a failed purge leaves the agent archived and can be retried. Archive
     * returns 409 for an already-archived agent; purge accepts any status.
     * @returns void
     * @throws ApiError
     */
    public static archiveAgent({
        agentId,
        purge = false,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
        /**
         * Default false: the agent is archived (terminal-but-kept; its row stays for history). true hard-deletes the agent from any status, together with its tokens, consent grants, permission grants and rules, bindings and connect sessions, and cancels its queued and running jobs. History (audit entries, events, execution records, finished jobs, notes) and the credentials it created are kept; once the agent is gone that history is visible to org:admin only.
         */
        purge?: boolean,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/agents/{agent_id}',
            path: {
                'agent_id': agentId,
            },
            query: {
                'purge': purge,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Get an agent
     * Get one agent's profile and lifecycle state.
     *
     * Returns the agent's name, description, owner, `status`, approval or
     * denial details, and whether it currently has an API key (`has_api_key`).
     * Readable by the agent itself, its owner, and `org:admin`; an agent outside
     * the caller's visibility answers 404.
     * @returns AgentResponse Successful Response
     * @throws ApiError
     */
    public static getAgent({
        agentId,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
    }): CancelablePromise<AgentResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/agents/{agent_id}',
            path: {
                'agent_id': agentId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Update an agent
     * Partially update an agent's `name`, `description` or `owner_id`.
     *
     * Only the fields present in the body change; an empty body returns the
     * agent unchanged. Allowed for the agent's owner or an `org:admin` (anyone
     * else gets 404). Changing `owner_id` transfers ownership, is
     * `org:admin`-only, revokes every active OAuth client grant bound to the
     * agent (so the new owner must re-consent any connected client), and
     * suspends active credential bindings to credentials outside the new
     * owner's ownership. The agent's API key and permissions are unaffected. An
     * archived agent cannot be updated (409). Returns the updated agent.
     * @returns AgentResponse Successful Response
     * @throws ApiError
     */
    public static updateAgent({
        agentId,
        requestBody,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
        /**
         * Request body for partially updating an agent.
         */
        requestBody: AgentPatchRequest,
    }): CancelablePromise<AgentResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/agents/{agent_id}',
            path: {
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Get an agent's API key metadata
     * Describe an agent's API key without revealing it.
     *
     * Returns the key record's `id`, `status` (`active`, or `revoked` once
     * revoked), `created_at`, `rotated_at` and `created_by`. The plaintext key
     * is never returned — it is shown once, by `:generate-api-key`. Metadata
     * stays available after revocation; the body is `null` if the agent never
     * had a key. Allowed for the agent's owner or an `org:admin`.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getAgentApiKeyInfo({
        agentId,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
    }): CancelablePromise<(ApiKeyInfoResponse | null)> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/agents/{agent_id}/api-key',
            path: {
                'agent_id': agentId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Get an agent's API key history
     * List recent API key rotations and revocations for an agent.
     *
     * Each entry gives the audit `action`, its `reason` (key rotated or key
     * revoked), the acting `actor_id`, and `occurred_at`. Drawn from the agent's
     * 50 most recent audit entries, newest first, so very old key events may
     * not appear; it is not paginated. Allowed for the agent's owner or an
     * `org:admin`.
     * @returns ApiKeyHistoryResponse Successful Response
     * @throws ApiError
     */
    public static getAgentApiKeyHistory({
        agentId,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
    }): CancelablePromise<ApiKeyHistoryResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/agents/{agent_id}/api-key/history',
            path: {
                'agent_id': agentId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * List an agent's credential bindings
     * List the credentials bound directly to an agent.
     *
     * Each binding names the credential, the API it serves, when it was bound,
     * whether it is `suspended` (and why), and any attached permission
     * `rule_set_id`. Credential secrets are never returned. Readable by the
     * agent itself, its owner, and `org:admin` (404 otherwise). The list is not
     * paginated.
     * @returns CredentialBindingListResponse Successful Response
     * @throws ApiError
     */
    public static listAgentCredentials({
        agentId,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
    }): CancelablePromise<CredentialBindingListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/agents/{agent_id}/credentials',
            path: {
                'agent_id': agentId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Bind a credential to an agent
     * Let an agent use a credential by binding it directly.
     *
     * A new binding starts with no permission rules, so the agent can make no
     * calls with it until rules are set or a rule set is attached. The caller
     * must be able to see the agent and must own the credential (or hold
     * `org:admin`). A credential that does not exist or that the caller does
     * not own returns 404; binding the same credential twice returns 409
     * (`credential_binding_conflict`).
     * Returns the new binding.
     * @returns CredentialBindingResponse Successful Response
     * @throws ApiError
     */
    public static bindAgentCredential({
        agentId,
        requestBody,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
        /**
         * Request body for directly binding a credential to an agent.
         */
        requestBody: CredentialBindRequest,
    }): CancelablePromise<CredentialBindingResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agents/{agent_id}/credentials',
            path: {
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Suspend or remove a credential binding
     * Stop an agent using a credential: suspend the binding, or purge it.
     *
     * By default the binding is **suspended**: the agent loses access at once,
     * but the binding and its permission rules are kept, and
     * `POST /agents/{agent_id}/credentials/{credential_id}:resume` restores it.
     * With `purge=true` the binding and its inline permission rules are deleted
     * outright, so a later re-bind starts from deny-all. An agent may suspend
     * its own binding but not purge it. Both modes are audited. Returns 204; a
     * missing binding returns 404.
     * @returns void
     * @throws ApiError
     */
    public static unbindAgentCredential({
        agentId,
        credentialId,
        purge = false,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
        /**
         * ID of the credential bound to the agent.
         */
        credentialId: string,
        /**
         * Default false: the binding is suspended (reversible; its permission rules survive and :resume restores access). true deletes the binding row outright, together with its inline permission rules.
         */
        purge?: boolean,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/agents/{agent_id}/credentials/{credential_id}',
            path: {
                'agent_id': agentId,
                'credential_id': credentialId,
            },
            query: {
                'purge': purge,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Resume a suspended credential binding
     * Restore an agent's access to a credential whose binding was suspended.
     *
     * Reverses the default (non-purge) unbind; a purged binding is gone and must
     * be bound again instead. Because resuming re-grants the credential, the
     * caller must own the credential (or hold `org:admin`) as well as see the
     * agent, and an agent cannot resume its own binding. Returns the binding;
     * 404 when no binding exists.
     * @returns CredentialBindingResponse Successful Response
     * @throws ApiError
     */
    public static resumeAgentCredentialBinding({
        agentId,
        credentialId,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
        /**
         * ID of the credential bound to the agent.
         */
        credentialId: string,
    }): CancelablePromise<CredentialBindingResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agents/{agent_id}/credentials/{credential_id}:resume',
            path: {
                'agent_id': agentId,
                'credential_id': credentialId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Update Agent Jwks
     * Update an agent's JWKS (public keys for JWT-bearer authentication).
     *
     * The JWKS must contain at least one Ed25519 public key and must not contain
     * any private key material. This enables the agent to authenticate via
     * JWT-bearer assertions signed with the corresponding private key.
     * @returns AgentResponse Successful Response
     * @throws ApiError
     */
    public static updateAgentJwks({
        agentId,
        requestBody,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
        /**
         * Request body for updating an agent's JWKS (public keys).
         *
         * The JWKS must contain at least one Ed25519 public key and must not
         * contain any private key material.
         */
        requestBody: JwksUpdateRequest,
    }): CancelablePromise<AgentResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/agents/{agent_id}/jwks',
            path: {
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * List an agent's connected OAuth clients
     * List the OAuth clients a user has consented to act as this agent.
     *
     * Each grant gives the client's display name and redirect-URI origin, the
     * granted scopes, the consenting user, its `status`, created and last-used
     * timestamps, and `can_revoke` for the caller. Filter by `status`
     * (`active` or `revoked`); results are cursor-paginated. Allowed for the
     * agent's owner or an admin; any other caller gets 404, the same as for an
     * agent that does not exist. Revoke a grant with
     * `POST /oauth-grants/{grant_id}:revoke`.
     * @returns OAuthGrantListResponse Successful Response
     * @throws ApiError
     */
    public static listAgentOauthGrants({
        agentId,
        status,
        limit = 50,
        cursor,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
        /**
         * Filter by grant lifecycle state.
         */
        status?: ('active' | 'revoked' | null),
        /**
         * Maximum number of grants to return (1-200, default 50).
         */
        limit?: number,
        /**
         * Cursor from a previous page's `next_cursor`; omit for the first page.
         */
        cursor?: (string | null),
    }): CancelablePromise<OAuthGrantListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/agents/{agent_id}/oauth-grants',
            path: {
                'agent_id': agentId,
            },
            query: {
                'status': status,
                'limit': limit,
                'cursor': cursor,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `Not Found`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Get Agent Permissions
     * List permissions granted to an agent.
     * @returns AgentPermissionsResponse Successful Response
     * @throws ApiError
     */
    public static getAgentPermissions({
        agentId,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
    }): CancelablePromise<AgentPermissionsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/agents/{agent_id}/permissions',
            path: {
                'agent_id': agentId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Replace Agent Permissions
     * Replace all permissions for an agent.
     * @returns AgentPermissionsResponse Successful Response
     * @throws ApiError
     */
    public static replaceAgentPermissions({
        agentId,
        requestBody,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
        /**
         * Request body for replacing an agent's permissions.
         */
        requestBody: AgentPermissionsRequest,
    }): CancelablePromise<AgentPermissionsResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/agents/{agent_id}/permissions',
            path: {
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Approve Agent
     * Approve a pending agent.
     *
     * Allowed for the agent's owner or an ``org:admin``. An agent with no owner
     * (an unclaimed self-registration) can be approved only by an ``org:admin``,
     * who becomes its owner. Any other caller gets a 404, whatever the agent's
     * status, so the response does not reveal agents outside the caller's
     * ownership.
     * @returns AgentResponse Successful Response
     * @throws ApiError
     */
    public static approveAgent({
        agentId,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
    }): CancelablePromise<AgentResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agents/{agent_id}:approve',
            path: {
                'agent_id': agentId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Claim Agent
     * Claim ownership of a self-registered agent using its claim token.
     *
     * Authenticated by the platform bearer token but requires **no** agent
     * permission — the single-use claim token minted at ``/register`` is the proof,
     * so the registering human (even a plain member) can take ownership. Sets
     * ``owner_id`` to the caller; the existing scoping + approve paths then apply.
     *
     * Restricted to ``USER`` actors: ``Agent.owner_id`` is a FK to ``users.id``, so
     * only a human can own an agent. The ``require_actor_type`` gate rejects a
     * non-user actor (an agent) at the boundary with a 403;
     * ``AgentService.claim`` re-checks the same invariant as defense-in-depth.
     *
     * ``allow_expired_password=True`` is intentional (matching ``GET /agents/{id}``):
     * claiming is an onboarding step a brand-new user may hit before they have
     * rotated a temporary password, so a must-change-password state must not block
     * it. The claim only sets ownership — it grants no permissions and cannot act as the
     * agent — so allowing it under an expired password is low-risk.
     * @returns AgentResponse Successful Response
     * @throws ApiError
     */
    public static claimAgent({
        agentId,
        requestBody,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
        /**
         * Request body for claiming ownership of a self-registered agent.
         */
        requestBody: ClaimRequest,
    }): CancelablePromise<AgentResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agents/{agent_id}:claim',
            path: {
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Deny Agent
     * Deny a pending agent.
     *
     * Same authorization as approve: the agent's owner or an ``org:admin``, and
     * only an ``org:admin`` for an agent with no owner. Any other caller gets a
     * 404.
     * @returns AgentResponse Successful Response
     * @throws ApiError
     */
    public static denyAgent({
        agentId,
        requestBody,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
        /**
         * Request body for denying an agent.
         */
        requestBody: DenyRequest,
    }): CancelablePromise<AgentResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agents/{agent_id}:deny',
            path: {
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Disable Agent
     * Disable an active agent.
     * @returns void
     * @throws ApiError
     */
    public static disableAgent({
        agentId,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agents/{agent_id}:disable',
            path: {
                'agent_id': agentId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Enable Agent
     * Enable a disabled agent.
     * @returns void
     * @throws ApiError
     */
    public static enableAgent({
        agentId,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agents/{agent_id}:enable',
            path: {
                'agent_id': agentId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Generate Agent Api Key
     * Generate a new API key for an active agent. Rotates any existing key.
     * @returns ApiKeyResponse Successful Response
     * @throws ApiError
     */
    public static generateAgentApiKey({
        agentId,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
    }): CancelablePromise<ApiKeyResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agents/{agent_id}:generate-api-key',
            path: {
                'agent_id': agentId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Revoke Agent Api Key
     * Revoke an agent's API key without generating a new one.
     * @returns void
     * @throws ApiError
     */
    public static revokeAgentApiKey({
        agentId,
    }: {
        /**
         * ID of the agent.
         */
        agentId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agents/{agent_id}:revoke-api-key',
            path: {
                'agent_id': agentId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
