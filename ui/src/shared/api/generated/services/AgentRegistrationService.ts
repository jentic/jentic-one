/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { RegisterRequest } from '../models/RegisterRequest';
import type { RegisterResponse } from '../models/RegisterResponse';
import type { RegistrationStatusResponse } from '../models/RegistrationStatusResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AgentRegistrationService {
    /**
     * Register Endpoint
     * Register a new agent client (RFC 7591).
     * @returns RegisterResponse Successful Response
     * @throws ApiError
     */
    public static registerEndpoint({
        requestBody,
    }: {
        /**
         * POST /register request body.
         */
        requestBody: RegisterRequest,
    }): CancelablePromise<RegisterResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/register',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Delete Registration Endpoint
     * Client deletion not supported — returns 403.
     * @returns void
     * @throws ApiError
     */
    public static deleteRegistrationEndpoint({
        agentId,
    }: {
        /**
         * ID of the registered agent (its client_id).
         */
        agentId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/register/{agent_id}',
            path: {
                'agent_id': agentId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Client deletion is not supported. This endpoint exists to refuse the RFC 7592 delete call, so it has no success response: every request answers 403 and the registration remains. Archive the agent instead.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Poll Status Endpoint
     * Poll registration status (RFC 7592).
     * @returns RegistrationStatusResponse Successful Response
     * @throws ApiError
     */
    public static pollStatusEndpoint({
        agentId,
    }: {
        /**
         * ID of the registered agent (its client_id).
         */
        agentId: string,
    }): CancelablePromise<RegistrationStatusResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/register/{agent_id}',
            path: {
                'agent_id': agentId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Update Registration Endpoint
     * Client update not supported — returns 403.
     * @returns void
     * @throws ApiError
     */
    public static updateRegistrationEndpoint({
        agentId,
    }: {
        /**
         * ID of the registered agent (its client_id).
         */
        agentId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/register/{agent_id}',
            path: {
                'agent_id': agentId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Client update is not supported. This endpoint exists to refuse the RFC 7592 update call, so it has no success response: every request answers 403 and nothing is changed. Re-register instead.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
