/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { McpConfigRegistrationRequest } from '../models/McpConfigRegistrationRequest';
import type { McpConfigRegistrationResponse } from '../models/McpConfigRegistrationResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class McpService {
    /**
     * Report MCP config registration
     * Record that an MCP server entry was written for an agent runtime.
     *
     * Called by `jentic setup` / `jentic skill init` once per runtime whose MCP
     * config entry it wrote (best-effort on the CLI side — a failed report never
     * blocks setup). Emits the `mcp.config_registered` internal event; when the
     * operator opted into telemetry, the event is forwarded carrying at most the
     * closed runtime tag. Reports are throttled per (actor, runtime) within a
     * 24h in-process window — a throttled repeat is still a 202 with
     * `recorded: false`, since the CLI treats the report as fire-and-forget.
     * Any authenticated actor may report.
     * @returns McpConfigRegistrationResponse Successful Response
     * @throws ApiError
     */
    public static reportMcpConfigRegistration({
        requestBody,
    }: {
        requestBody: McpConfigRegistrationRequest,
    }): CancelablePromise<McpConfigRegistrationResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/mcp/config-registrations',
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
}
