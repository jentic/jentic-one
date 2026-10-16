/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiKeyCreateRequest } from '../models/ApiKeyCreateRequest';
import type { ApiKeyUpdateRequest } from '../models/ApiKeyUpdateRequest';
import type { BasicAuthCreateRequest } from '../models/BasicAuthCreateRequest';
import type { BasicAuthUpdateRequest } from '../models/BasicAuthUpdateRequest';
import type { BearerTokenCreateRequest } from '../models/BearerTokenCreateRequest';
import type { BearerTokenUpdateRequest } from '../models/BearerTokenUpdateRequest';
import type { BindingAccessResponse } from '../models/BindingAccessResponse';
import type { ConnectRequestBody } from '../models/ConnectRequestBody';
import type { CredentialAgentListResponse } from '../models/CredentialAgentListResponse';
import type { CredentialCreateResponse } from '../models/CredentialCreateResponse';
import type { CredentialListResponse } from '../models/CredentialListResponse';
import type { CredentialRedactedResponse } from '../models/CredentialRedactedResponse';
import type { NoAuthCreateRequest } from '../models/NoAuthCreateRequest';
import type { OAuth2CreateRequest } from '../models/OAuth2CreateRequest';
import type { OAuth2UpdateRequest } from '../models/OAuth2UpdateRequest';
import type { PermissionRuleListResponse } from '../models/PermissionRuleListResponse';
import type { PermissionRuleSchema } from '../models/PermissionRuleSchema';
import type { PermissionsPatchRequest } from '../models/PermissionsPatchRequest';
import type { PermissionTestRequest } from '../models/PermissionTestRequest';
import type { PermissionTestResponse } from '../models/PermissionTestResponse';
import type { ProviderDiscoveryResponse } from '../models/ProviderDiscoveryResponse';
import type { RuleSetAttachRequest } from '../models/RuleSetAttachRequest';
import type { Sigv4CreateRequest } from '../models/Sigv4CreateRequest';
import type { Sigv4UpdateRequest } from '../models/Sigv4UpdateRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class CredentialsService {
    /**
     * List credentials
     * List credentials with cursor-based pagination.
     * @returns CredentialListResponse Successful Response
     * @throws ApiError
     */
    public static listCredentials({
        cursor,
        limit = 50,
        vendor,
    }: {
        /**
         * Opaque cursor from a previous page's next_cursor; omit for the first page.
         */
        cursor?: (string | null),
        /**
         * Maximum number of credentials to return (1-200, default 50).
         */
        limit?: number,
        /**
         * Filter to credentials for this API vendor (exact match).
         */
        vendor?: (string | null),
    }): CancelablePromise<CredentialListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/credentials',
            query: {
                'cursor': cursor,
                'limit': limit,
                'vendor': vendor,
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
     * Create credential
     * Create a new credential. The secret is returned once and never readable again.
     * @returns CredentialCreateResponse Successful Response
     * @throws ApiError
     */
    public static createCredential({
        requestBody,
    }: {
        /**
         * Request payload for the `createCredential` operation.
         */
        requestBody: (BearerTokenCreateRequest | ApiKeyCreateRequest | BasicAuthCreateRequest | OAuth2CreateRequest | NoAuthCreateRequest | Sigv4CreateRequest),
    }): CancelablePromise<CredentialCreateResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/credentials',
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
     * List credential providers
     * Return discovery metadata for all configured credential providers.
     * @returns ProviderDiscoveryResponse Successful Response
     * @throws ApiError
     */
    public static listProviders(): CancelablePromise<ProviderDiscoveryResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/credentials/providers',
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
     * Delete credential
     * Delete a credential.
     * @returns void
     * @throws ApiError
     */
    public static deleteCredential({
        credentialId,
    }: {
        /**
         * ID of the credential.
         */
        credentialId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/credentials/{credential_id}',
            path: {
                'credential_id': credentialId,
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
     * Get credential
     * Get a single credential with redacted secrets.
     * @returns CredentialRedactedResponse Successful Response
     * @throws ApiError
     */
    public static getCredential({
        credentialId,
    }: {
        /**
         * ID of the credential.
         */
        credentialId: string,
    }): CancelablePromise<CredentialRedactedResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/credentials/{credential_id}',
            path: {
                'credential_id': credentialId,
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
     * Update or rotate credential
     * Update or rotate a credential.
     * @returns CredentialRedactedResponse Successful Response
     * @throws ApiError
     */
    public static updateCredential({
        credentialId,
        requestBody,
    }: {
        /**
         * ID of the credential.
         */
        credentialId: string,
        /**
         * Request payload for the `updateCredential` operation.
         */
        requestBody: (BearerTokenUpdateRequest | ApiKeyUpdateRequest | BasicAuthUpdateRequest | OAuth2UpdateRequest | Sigv4UpdateRequest),
    }): CancelablePromise<CredentialRedactedResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/credentials/{credential_id}',
            path: {
                'credential_id': credentialId,
            },
            body: requestBody,
            mediaType: 'application/json',
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
     * List agents bound to credential
     * List agents directly bound to a credential with cursor-based pagination.
     *
     * The reverse lookup for the credential-detail "Agents" view: every agent
     * whose direct binding points at this credential, filtered to the agents
     * the caller may see.
     * Suspended bindings are included with their flag set and, when known, the
     * reason (`api_deleted` when the credential's API was deleted).
     * @returns CredentialAgentListResponse Successful Response
     * @throws ApiError
     */
    public static listCredentialAgents({
        credentialId,
        cursor,
        limit = 50,
    }: {
        /**
         * ID of the credential.
         */
        credentialId: string,
        /**
         * Opaque cursor from a previous page's next_cursor; omit for the first page.
         */
        cursor?: (string | null),
        /**
         * Maximum number of agents to return (1-200, default 50).
         */
        limit?: number,
    }): CancelablePromise<CredentialAgentListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/credentials/{credential_id}/agents',
            path: {
                'credential_id': credentialId,
            },
            query: {
                'cursor': cursor,
                'limit': limit,
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
     * List binding permission rules
     * List the ordered PBAC rules for a direct `(agent, credential)` binding.
     *
     * Also returns the rest of what the binding grants: the shared rule set it
     * points at (whose rules apply instead of `data`), the credential's granted
     * OAuth scopes, and whether the binding is suspended. The bound agent can
     * read its own binding.
     * @returns BindingAccessResponse Successful Response
     * @throws ApiError
     */
    public static listAgentCredentialPermissions({
        credentialId,
        agentId,
    }: {
        /**
         * ID of the credential.
         */
        credentialId: string,
        /**
         * ID of the agent bound to the credential.
         */
        agentId: string,
    }): CancelablePromise<BindingAccessResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/credentials/{credential_id}/agents/{agent_id}/permissions',
            path: {
                'credential_id': credentialId,
                'agent_id': agentId,
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
     * Patch binding permission rules
     * Additively add and/or remove permission rules on a binding.
     * @returns PermissionRuleListResponse Successful Response
     * @throws ApiError
     */
    public static patchAgentCredentialPermissions({
        credentialId,
        agentId,
        requestBody,
    }: {
        /**
         * ID of the credential.
         */
        credentialId: string,
        /**
         * ID of the agent bound to the credential.
         */
        agentId: string,
        /**
         * Patch permission rules — add and/or remove.
         */
        requestBody: PermissionsPatchRequest,
    }): CancelablePromise<PermissionRuleListResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/credentials/{credential_id}/agents/{agent_id}/permissions',
            path: {
                'credential_id': credentialId,
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
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
     * Replace binding permission rules
     * Replace the full set of permission rules for a binding (idempotent PUT).
     * @returns PermissionRuleListResponse Successful Response
     * @throws ApiError
     */
    public static replaceAgentCredentialPermissions({
        credentialId,
        agentId,
        requestBody,
    }: {
        /**
         * ID of the credential.
         */
        credentialId: string,
        /**
         * ID of the agent bound to the credential.
         */
        agentId: string,
        /**
         * Request payload for the `replaceAgentCredentialPermissions` operation.
         */
        requestBody: Array<PermissionRuleSchema>,
    }): CancelablePromise<PermissionRuleListResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/credentials/{credential_id}/agents/{agent_id}/permissions',
            path: {
                'credential_id': credentialId,
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
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
     * Dry-run permission evaluation
     * Answer "what would the broker do for this request?" without calling upstream.
     *
     * Unlike the toolkit `:test` there is **no vendor pooling**: the direct
     * binding's rules are one ordered first-match-wins list, so the result is
     * exactly this binding's policy. Default-deny when nothing matches.
     * @returns PermissionTestResponse Successful Response
     * @throws ApiError
     */
    public static testAgentCredentialPermissions({
        credentialId,
        agentId,
        requestBody,
    }: {
        /**
         * ID of the credential.
         */
        credentialId: string,
        /**
         * ID of the agent bound to the credential.
         */
        agentId: string,
        /**
         * Request body for :test — dry-run a request shape against the binding's rules.
         */
        requestBody: PermissionTestRequest,
    }): CancelablePromise<PermissionTestResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/credentials/{credential_id}/agents/{agent_id}/permissions:test',
            path: {
                'credential_id': credentialId,
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
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
     * Detach rule set from binding
     * Detach the binding's shared rule set — its inline rules apply again.
     *
     * Idempotent: detaching a binding already on inline rules is a no-op 204.
     * @returns void
     * @throws ApiError
     */
    public static detachAgentCredentialRuleSet({
        credentialId,
        agentId,
    }: {
        /**
         * ID of the credential.
         */
        credentialId: string,
        /**
         * ID of the agent bound to the credential.
         */
        agentId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/credentials/{credential_id}/agents/{agent_id}/rule-set',
            path: {
                'credential_id': credentialId,
                'agent_id': agentId,
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
     * Attach rule set to binding
     * Point the binding at a shared rule set (idempotent PUT).
     *
     * While attached, the set's ordered list is the binding's effective policy
     * and its inline rules are dormant — `permissions:test` evaluates the set.
     * The set must exist (404 `rule_set_not_found`). The caller must be the
     * set's creator or an org admin, unless the set is curated (created by an
     * org admin), which any caller who may write the binding's rules can
     * attach; otherwise 403 `rule_set_attach_denied`. Re-attaching the set the
     * binding already points at is a no-op.
     * @returns void
     * @throws ApiError
     */
    public static attachAgentCredentialRuleSet({
        credentialId,
        agentId,
        requestBody,
    }: {
        /**
         * ID of the credential.
         */
        credentialId: string,
        /**
         * ID of the agent bound to the credential.
         */
        agentId: string,
        /**
         * Point a direct agent↔credential binding at a shared rule set.
         */
        requestBody: RuleSetAttachRequest,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/credentials/{credential_id}/agents/{agent_id}/rule-set',
            path: {
                'credential_id': credentialId,
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
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
     * Begin OAuth connect flow
     * Initiate the OAuth connect flow for a credential.
     *
     * Discriminates on the provider's returned challenge: OAuth2
     * authorization-code providers return an ``authorize_url`` for popup
     * redirect; device-flow providers return ``user_code`` /
     * ``verification_uri`` for the RFC 8628 human step.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static connectCredential({
        credentialId,
        requestBody,
    }: {
        /**
         * ID of the credential.
         */
        credentialId: string,
        /**
         * Request body for initiating a credential connect flow.
         */
        requestBody: ConnectRequestBody,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/credentials/{credential_id}/connect',
            path: {
                'credential_id': credentialId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `Not Found`,
                409: `Credential is not connectable`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
