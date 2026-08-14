/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AgentCreateRequest } from '../models/AgentCreateRequest';
import type { AgentListResponse } from '../models/AgentListResponse';
import type { AgentPatchRequest } from '../models/AgentPatchRequest';
import type { AgentResponse } from '../models/AgentResponse';
import type { AgentScopesRequest } from '../models/AgentScopesRequest';
import type { AgentScopesResponse } from '../models/AgentScopesResponse';
import type { ApiKeyHistoryResponse } from '../models/ApiKeyHistoryResponse';
import type { ApiKeyInfoResponse } from '../models/ApiKeyInfoResponse';
import type { ApiKeyResponse } from '../models/ApiKeyResponse';
import type { ClaimRequest } from '../models/ClaimRequest';
import type { jentic_one__auth__web__schemas__agents__DenyRequest } from '../models/jentic_one__auth__web__schemas__agents__DenyRequest';
import type { ToolkitBindingListResponse } from '../models/ToolkitBindingListResponse';
import type { ToolkitBindingResponse } from '../models/ToolkitBindingResponse';
import type { ToolkitBindRequest } from '../models/ToolkitBindRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AgentsService {
    /**
     * List Agents
     * List agents — scoped by identity via dynamic query scoping.
     * @returns AgentListResponse Successful Response
     * @throws ApiError
     */
    public static listAgents({
        cursor,
        limit = 50,
        status,
    }: {
        cursor?: (string | null),
        limit?: number,
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
     * Create Agent
     * Create a new agent manually.
     * @returns AgentResponse Successful Response
     * @throws ApiError
     */
    public static createAgent({
        requestBody,
    }: {
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
     * Soft-archive an agent — revokes scope grants and toolkit bindings.
     * @returns void
     * @throws ApiError
     */
    public static archiveAgent({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
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
     * Get Agent
     * Get agent by ID — requires agents:read or self-read.
     * @returns AgentResponse Successful Response
     * @throws ApiError
     */
    public static getAgent({
        agentId,
    }: {
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
     * Update Agent
     * Partially update an agent — name, description, or owner_id.
     * @returns AgentResponse Successful Response
     * @throws ApiError
     */
    public static updateAgent({
        agentId,
        requestBody,
    }: {
        agentId: string,
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
     * Get Agent Api Key Info
     * Get API key metadata for an agent. Returns info even after revocation.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getAgentApiKeyInfo({
        agentId,
    }: {
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
     * Get Agent Api Key History
     * Get the audit history of API key operations for an agent.
     * @returns ApiKeyHistoryResponse Successful Response
     * @throws ApiError
     */
    public static getAgentApiKeyHistory({
        agentId,
    }: {
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
     * Get Agent Scopes
     * List scopes granted to an agent.
     * @returns AgentScopesResponse Successful Response
     * @throws ApiError
     */
    public static getAgentScopes({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<AgentScopesResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/agents/{agent_id}/scopes',
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
     * Replace Agent Scopes
     * Replace all scopes for an agent.
     * @returns AgentScopesResponse Successful Response
     * @throws ApiError
     */
    public static replaceAgentScopes({
        agentId,
        requestBody,
    }: {
        agentId: string,
        requestBody: AgentScopesRequest,
    }): CancelablePromise<AgentScopesResponse> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/agents/{agent_id}/scopes',
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
     * List Toolkits
     * List toolkit bindings for an agent — requires agents:read or self.
     * @returns ToolkitBindingListResponse Successful Response
     * @throws ApiError
     */
    public static listAgentToolkits({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<ToolkitBindingListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/agents/{agent_id}/toolkits',
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
     * Bind Toolkit
     * Bind a toolkit to an agent.
     * @returns ToolkitBindingResponse Successful Response
     * @throws ApiError
     */
    public static bindToolkit({
        agentId,
        requestBody,
    }: {
        agentId: string,
        requestBody: ToolkitBindRequest,
    }): CancelablePromise<ToolkitBindingResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agents/{agent_id}/toolkits',
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
     * Unbind Toolkit
     * Unbind a toolkit from an agent.
     * @returns void
     * @throws ApiError
     */
    public static unbindToolkit({
        agentId,
        toolkitId,
    }: {
        agentId: string,
        toolkitId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/agents/{agent_id}/toolkits/{toolkit_id}',
            path: {
                'agent_id': agentId,
                'toolkit_id': toolkitId,
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
     * Approve Agent
     * Approve a pending agent.
     * @returns AgentResponse Successful Response
     * @throws ApiError
     */
    public static approveAgent({
        agentId,
    }: {
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
     * ``allow_expired_password=True`` is intentional (matching ``GET /agents/{id}``):
     * claiming is an onboarding step a brand-new user may hit before they have
     * rotated a temporary password, so a must-change-password state must not block
     * it. The claim only sets ownership — it grants no scopes and cannot act as the
     * agent — so allowing it under an expired password is low-risk.
     * @returns AgentResponse Successful Response
     * @throws ApiError
     */
    public static claimAgent({
        agentId,
        requestBody,
    }: {
        agentId: string,
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
     * @returns AgentResponse Successful Response
     * @throws ApiError
     */
    public static denyAgent({
        agentId,
        requestBody,
    }: {
        agentId: string,
        requestBody: jentic_one__auth__web__schemas__agents__DenyRequest,
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
