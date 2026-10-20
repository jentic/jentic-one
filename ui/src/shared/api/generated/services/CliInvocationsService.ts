/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CliInvocationCreatedResponse } from '../models/CliInvocationCreatedResponse';
import type { CliInvocationCreateRequest } from '../models/CliInvocationCreateRequest';
import type { CliInvocationResponse } from '../models/CliInvocationResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class CliInvocationsService {
    /**
     * Mint a CLI invocation token
     * Mint a short-lived invocation token for one vendor CLI command.
     *
     * Called by `jentic gh`/`cf`/`aws` before it runs the vendor CLI. Only an
     * agent holding `capabilities:execute` and authenticated with its own
     * access token (`at_…`, not an API key or JWT) may mint; anything else
     * answers 403 (`cli_invocation_mint_forbidden`). The token lives
     * `cli_facades.invocation_ttl_seconds` (renew it with `:renew`), never past
     * `hard_expires_at`, and never past the access token it was minted with:
     * revoking or expiring that access token ends the invocation token too.
     * It is accepted only by the Broker's CLI facade for `vendor`; `/execute`,
     * the control plane and MCP reject it. Answers 404 while
     * `cli_facades.enabled` or the vendor's facade is off. Minting is rate
     * limited per agent (`cli_facades.mint_rate_limit_per_minute`).
     * @returns CliInvocationCreatedResponse Successful Response
     * @throws ApiError
     */
    public static createCliInvocation({
        requestBody,
    }: {
        /**
         * Request body for minting a CLI invocation token.
         */
        requestBody: CliInvocationCreateRequest,
    }): CancelablePromise<CliInvocationCreatedResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/cli-invocations',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `No CLI facade is available for \`vendor\` (\`cli_facade_not_found\`).`,
                422: `Unprocessable Entity`,
                429: `The agent's mint rate limit is exhausted; honour \`Retry-After\`.`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Renew a CLI invocation token
     * Extend one of the caller's live invocation tokens.
     *
     * Moves `expires_at` to `cli_facades.invocation_ttl_seconds` from now, capped at
     * `hard_expires_at`. The CLI wrapper calls this on a heartbeat while the
     * command runs. Only the agent that minted the invocation may renew it;
     * any other caller gets 404. A revoked or expired invocation answers 409.
     * @returns CliInvocationResponse Successful Response
     * @throws ApiError
     */
    public static renewCliInvocation({
        invocationId,
    }: {
        /**
         * ID of the CLI invocation.
         */
        invocationId: string,
    }): CancelablePromise<CliInvocationResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/cli-invocations/{invocation_id}:renew',
            path: {
                'invocation_id': invocationId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `The invocation does not exist or is not the caller's.`,
                409: `The invocation is revoked or expired (\`cli_invocation_not_renewable\`).`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Revoke a CLI invocation token
     * Revoke one of the caller's invocation tokens.
     *
     * The CLI wrapper calls this when the command exits. The Broker stops
     * accepting the token within its token-cache TTL
     * (`broker.resolve_cache_ttl_seconds`). Only the agent that minted the
     * invocation may revoke it; any other caller gets 404. Revoking an
     * already-revoked invocation succeeds.
     * @returns void
     * @throws ApiError
     */
    public static revokeCliInvocation({
        invocationId,
    }: {
        /**
         * ID of the CLI invocation.
         */
        invocationId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/cli-invocations/{invocation_id}:revoke',
            path: {
                'invocation_id': invocationId,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `The invocation does not exist or is not the caller's.`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
