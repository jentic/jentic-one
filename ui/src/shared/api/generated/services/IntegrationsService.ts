/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ApiKeyConfirmSessionRequest } from '../models/ApiKeyConfirmSessionRequest';
import type { BasicConfirmSessionRequest } from '../models/BasicConfirmSessionRequest';
import type { BearerConfirmSessionRequest } from '../models/BearerConfirmSessionRequest';
import type { ConnectSessionListResponse } from '../models/ConnectSessionListResponse';
import type { ExistingCredentialConfirmSessionRequest } from '../models/ExistingCredentialConfirmSessionRequest';
import type { IntegrationsConnectRequest } from '../models/IntegrationsConnectRequest';
import type { OAuthConfirmSessionRequest } from '../models/OAuthConfirmSessionRequest';
import type { OwnOAuthClientConfirmSessionRequest } from '../models/OwnOAuthClientConfirmSessionRequest';
import type { ReauthorizeConfirmSessionRequest } from '../models/ReauthorizeConfirmSessionRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class IntegrationsService {
    /**
     * List connect sessions
     * List connect sessions with cursor-based pagination.
     *
     * Rows are slim summaries scoped to the caller (initiator-owned;
     * ``org:admin`` sees all; a delegated agent holding
     * ``owner:credentials:read`` also sees its owner's sessions). The
     * ``poll_token`` capability is never included.
     * @returns ConnectSessionListResponse Successful Response
     * @throws ApiError
     */
    public static listConnectSessions({
        state,
        vendor,
        cursor,
        limit = 50,
    }: {
        /**
         * Filter by session state
         */
        state?: ('created' | 'awaiting_app' | 'polling' | 'connected' | null),
        /**
         * Filter by vendor registry key
         */
        vendor?: (string | null),
        cursor?: (string | null),
        limit?: number,
    }): CancelablePromise<ConnectSessionListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/connect-sessions',
            query: {
                'state': state,
                'vendor': vendor,
                'cursor': cursor,
                'limit': limit,
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
     * Get review data for a connect session
     * Data the review page needs: vendor display name, resolved flow, the
     * scope catalog flagged with default/requested, current state, reason.
     *
     * Gated by the session's ``poll_token`` capability (rides the approval
     * URL / the ``:connect`` response), or by being the target agent's owner
     * or ``org:admin`` — ``credentials:write`` alone must not read arbitrary
     * sessions' review data. Missing session, token mismatch and a caller who
     * is neither all surface as 403, matching ``/status`` (no session-id
     * enumeration oracle).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static getConnectSession({
        sessionId,
        pollToken,
    }: {
        sessionId: string,
        /**
         * Opaque poll capability returned by :connect. Optional for the target agent's owner (with credentials:write and agents:write) and org:admin
         */
        pollToken?: (string | null),
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/connect-sessions/{session_id}',
            path: {
                'session_id': sessionId,
            },
            query: {
                'poll_token': pollToken,
            },
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                409: `The session's OAuth app changed; start a new session`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Poll a connect session's status
     * @returns any Successful Response
     * @throws ApiError
     */
    public static pollConnectSessionStatus({
        sessionId,
        pollToken,
    }: {
        sessionId: string,
        /**
         * Opaque poll capability returned by :connect. Optional for the target agent's owner (with credentials:write and agents:write) and org:admin
         */
        pollToken?: (string | null),
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/connect-sessions/{session_id}/status',
            path: {
                'session_id': sessionId,
            },
            query: {
                'poll_token': pollToken,
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
     * Cancel an in-flight connect session
     * Terminate a still-active session at the user's request.
     *
     * Gated like ``/status`` — the ``poll_token`` the SPA already holds, or
     * the target agent's owner / ``org:admin`` without it.
     *
     * A still-existing but already-terminal session is a 204 no-op — a
     * "Cancel" click racing the poll scanner doesn't error. A session
     * that has already been cascade-deleted (unhappy-terminal path in
     * ``_mark_terminal``) surfaces as 403, matching ``/status`` — the
     * caller can't distinguish "gone" from "your poll_token is wrong",
     * which is the enumeration-oracle guard. The SPA's cancel-on-unmount
     * is fire-and-forget and ``.catch``es the 403, so this doesn't leak
     * into the UX.
     * @returns void
     * @throws ApiError
     */
    public static cancelConnectSession({
        sessionId,
        pollToken,
    }: {
        sessionId: string,
        /**
         * Opaque poll capability returned by :connect. Optional for the target agent's owner (with credentials:write and agents:write) and org:admin
         */
        pollToken?: (string | null),
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/connect-sessions/{session_id}:cancel',
            path: {
                'session_id': sessionId,
            },
            query: {
                'poll_token': pollToken,
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
     * Confirm a connect session
     * Called by the review page after the human confirms selections.
     *
     * The body's ``kind`` picks the variant (no ``kind`` is the OAuth
     * variant): OAuth scopes, a secret for a ``manual_*`` session
     * (``api_key`` / ``bearer`` / ``basic``), the approver's own OAuth client
     * for an ``awaiting_app`` session, or a credential the approver already
     * holds (``existing_credential``, ``reauthorize``). Secrets are write-only:
     * they never appear in a response, audit entry or log.
     *
     * Shares the ``:connect`` per-actor rate bucket — this is the endpoint
     * that actually fires the vendor's device-authorization call, and a
     * failed ``begin`` leaves the session retryable, so it must not be
     * free to hammer during a vendor incident. Gated like the review read
     * (``poll_token`` or owner / ``org:admin``; 403 otherwise).
     * @returns any Successful Response
     * @throws ApiError
     */
    public static confirmConnectSession({
        sessionId,
        requestBody,
        pollToken,
    }: {
        sessionId: string,
        requestBody: (OAuthConfirmSessionRequest | ApiKeyConfirmSessionRequest | BearerConfirmSessionRequest | BasicConfirmSessionRequest | OwnOAuthClientConfirmSessionRequest | ExistingCredentialConfirmSessionRequest | ReauthorizeConfirmSessionRequest),
        /**
         * Opaque poll capability returned by :connect. Optional for the target agent's owner (with credentials:write and agents:write) and org:admin
         */
        pollToken?: (string | null),
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/connect-sessions/{session_id}:confirm',
            path: {
                'session_id': sessionId,
            },
            query: {
                'poll_token': pollToken,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `Not Found`,
                409: `Session not awaiting confirmation, its OAuth app, scheme or hosts changed, or the review is stale`,
                422: `Unprocessable Entity`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
    /**
     * Reject an agent's connect request
     * End the session as rejected — the approve dialog's explicit Reject.
     *
     * Only the target agent's owner (holding ``credentials:write`` and
     * ``agents:write``) or ``org:admin``; there is no poll-token path and an
     * agent can never reject. Everyone else gets the uniform 403. The agent's
     * ``/status`` then reports ``failed`` with ``error_code: rejected``, and
     * its repeat ``:connect`` for the same target answers 429
     * ``recently_rejected`` for the rejection cooldown. Closing the dialog
     * without rejecting makes no call; an unmount cancel stays ``cancelled``.
     * @returns void
     * @throws ApiError
     */
    public static rejectConnectSession({
        sessionId,
    }: {
        sessionId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/connect-sessions/{session_id}:reject',
            path: {
                'session_id': sessionId,
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
     * Start an integration connect session
     * Both entrypoints (agent + UI) use this endpoint.
     *
     * Agent callers: `agent_id` in the payload is refused (the caller *is*
     * the agent — spoofing another agent's id is a permission-boundary
     * violation). The caller's own identity is injected instead. UI / user
     * callers: `agent_id` is optional — when named, confirm creates the
     * direct agent-credential binding + permission rules; when omitted, the
     * credential connects unbound and an agent can be bound later through
     * the credentials API.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static integrationsConnect({
        requestBody,
    }: {
        requestBody: IntegrationsConnectRequest,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/integrations:connect',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `Unknown vendor or API, or API targets are not enabled`,
                409: `The API declares no supported scheme, or its hosts cannot be pinned`,
                422: `Unprocessable Entity`,
                429: `Rate limit, open-session cap, or a recent rejection`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
