/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GrantBody } from '../models/GrantBody';
import type { GrantsReplaceBody } from '../models/GrantsReplaceBody';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AgentsService {
    /**
     * List Agents
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listAgentsAgentsGet({
        view = 'active',
        status,
    }: {
        /**
         * active: not denied and not deregistered; declined: denied only; removed: soft-deleted (deregistered)
         */
        view?: 'active' | 'declined' | 'removed',
        /**
         * When view=active only: pending, approved, disabled
         */
        status?: (string | null),
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/agents',
            query: {
                'view': view,
                'status': status,
            },
            errors: {
                403: `Human session required.`,
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Agent
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getAgentAgentsAgentIdGet({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/agents/{agent_id}',
            path: {
                'agent_id': agentId,
            },
            errors: {
                403: `Human session required.`,
                404: `Agent or toolkit not found.`,
                422: `Validation Error`,
            },
        });
    }
    /**
     * Deregister agent (soft delete)
     * Soft-delete for audit: revoke tokens, strip JWKS and registration secrets, drop grants.
     * @returns void
     * @throws ApiError
     */
    public static deleteAgentAgentsAgentIdDelete({
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
                403: `Human session required.`,
                404: `Agent or toolkit not found.`,
                422: `Validation Error`,
            },
        });
    }
    /**
     * Approve Agent
     * @returns any Successful Response
     * @throws ApiError
     */
    public static approveAgentAgentsAgentIdApprovePost({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agents/{agent_id}/approve',
            path: {
                'agent_id': agentId,
            },
            errors: {
                400: `Agent is not in 'pending' status.`,
                403: `Human session required.`,
                404: `Agent or toolkit not found.`,
                422: `Validation Error`,
            },
        });
    }
    /**
     * Decline registration
     * @returns any Successful Response
     * @throws ApiError
     */
    public static denyAgentAgentsAgentIdDenyPost({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agents/{agent_id}/deny',
            path: {
                'agent_id': agentId,
            },
            errors: {
                400: `Agent is not in 'pending' status.`,
                403: `Human session required.`,
                404: `Agent or toolkit not found.`,
                422: `Validation Error`,
            },
        });
    }
    /**
     * Disable Agent
     * @returns any Successful Response
     * @throws ApiError
     */
    public static disableAgentAgentsAgentIdDisablePost({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agents/{agent_id}/disable',
            path: {
                'agent_id': agentId,
            },
            errors: {
                403: `Human session required.`,
                404: `Agent or toolkit not found.`,
                422: `Validation Error`,
            },
        });
    }
    /**
     * Enable Agent
     * @returns any Successful Response
     * @throws ApiError
     */
    public static enableAgentAgentsAgentIdEnablePost({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agents/{agent_id}/enable',
            path: {
                'agent_id': agentId,
            },
            errors: {
                400: `Agent is not currently disabled.`,
                403: `Human session required.`,
                404: `Agent or toolkit not found.`,
                422: `Validation Error`,
            },
        });
    }
    /**
     * Add Grant
     * @returns any Successful Response
     * @throws ApiError
     */
    public static addGrantAgentsAgentIdGrantsPost({
        agentId,
        requestBody,
    }: {
        agentId: string,
        requestBody: GrantBody,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/agents/{agent_id}/grants',
            path: {
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                403: `Human session required.`,
                404: `Agent or toolkit not found.`,
                409: `Conflicting state (e.g. disabled toolkit).`,
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Grants
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listGrantsAgentsAgentIdGrantsGet({
        agentId,
    }: {
        agentId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/agents/{agent_id}/grants',
            path: {
                'agent_id': agentId,
            },
            errors: {
                403: `Human session required.`,
                422: `Validation Error`,
            },
        });
    }
    /**
     * Replace the agent's grants atomically
     * Replace the agent's full grant set in a single transaction.
     *
     * Used by the admin UI's grant-edit flow: the user picks a set of toolkits
     * in a dialog and submits the whole set in one call, instead of dispatching
     * a stream of POST/DELETE requests sequentially. A 5xx mid-operation under
     * the old flow would leave the agent in a partial state — this endpoint
     * eliminates that window.
     *
     * Behaviour:
     *
     * * Adds toolkits in ``toolkit_ids`` that the agent doesn't already hold.
     * Disabled toolkits and unknown toolkit_ids reject the **whole** call.
     * * Removes existing grants not in ``toolkit_ids``.
     * * Preserves ``granted_at`` / ``granted_by`` for grants that survive
     * (the conflict path is a no-op, exactly like POST).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static replaceGrantsAgentsAgentIdGrantsPut({
        agentId,
        requestBody,
    }: {
        agentId: string,
        requestBody: GrantsReplaceBody,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/agents/{agent_id}/grants',
            path: {
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                403: `Human session required.`,
                404: `Agent or toolkit not found.`,
                409: `Conflicting state (e.g. disabled toolkit).`,
                422: `Validation Error`,
            },
        });
    }
    /**
     * Rotate Agent Jwks
     * @returns any Successful Response
     * @throws ApiError
     */
    public static rotateAgentJwksAgentsAgentIdJwksPut({
        agentId,
        requestBody,
    }: {
        agentId: string,
        requestBody: Record<string, any>,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/agents/{agent_id}/jwks',
            path: {
                'agent_id': agentId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Invalid or missing jwks.`,
                403: `Human session required.`,
                404: `Agent or toolkit not found.`,
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Grant
     * @returns void
     * @throws ApiError
     */
    public static deleteGrantAgentsAgentIdGrantsToolkitIdDelete({
        agentId,
        toolkitId,
    }: {
        agentId: string,
        /**
         * Toolkit id to revoke
         */
        toolkitId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/agents/{agent_id}/grants/{toolkit_id}',
            path: {
                'agent_id': agentId,
                'toolkit_id': toolkitId,
            },
            errors: {
                403: `Human session required.`,
                404: `Agent or toolkit not found.`,
                422: `Validation Error`,
            },
        });
    }
    /**
     * List agents granted access to this toolkit
     * List the OAuth agents currently granted access to this toolkit.
     *
     * The reverse of ``GET /agents/{agent_id}/grants`` — given a toolkit, return
     * every active (non-deregistered) agent that holds a grant on it, so the
     * toolkit detail view can show its bound agents without an N+1 client fan-out.
     * Admin-only.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listToolkitAgentsToolkitsToolkitIdAgentsGet({
        toolkitId,
    }: {
        /**
         * Toolkit ID (e.g. 'default' or custom toolkit identifier)
         */
        toolkitId: string,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/toolkits/{toolkit_id}/agents',
            path: {
                'toolkit_id': toolkitId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
