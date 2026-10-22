/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AmendSessionRequest } from '../models/AmendSessionRequest';
import type { ApiKeyConfirmSessionRequest } from '../models/ApiKeyConfirmSessionRequest';
import type { BasicConfirmSessionRequest } from '../models/BasicConfirmSessionRequest';
import type { BearerConfirmSessionRequest } from '../models/BearerConfirmSessionRequest';
import type { ConnectSessionListResponse } from '../models/ConnectSessionListResponse';
import type { EditConfirmSessionRequest } from '../models/EditConfirmSessionRequest';
import type { EditConnectionRequest } from '../models/EditConnectionRequest';
import type { ExistingCredentialConfirmSessionRequest } from '../models/ExistingCredentialConfirmSessionRequest';
import type { IntegrationsConnectRequest } from '../models/IntegrationsConnectRequest';
import type { OAuthConfirmSessionRequest } from '../models/OAuthConfirmSessionRequest';
import type { OwnOAuthClientConfirmSessionRequest } from '../models/OwnOAuthClientConfirmSessionRequest';
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
        /**
         * Opaque cursor from a previous page's next_cursor; omit for the first page.
         */
        cursor?: (string | null),
        /**
         * Maximum number of sessions to return (1-200, default 50).
         */
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
        /**
         * ID of the connect session.
         */
        sessionId: string,
        /**
         * Opaque poll capability returned by :connect. Optional for the target agent's owner (with credentials:write and agents:write) and org:admin. Deliberately a query parameter: it rides the approval URL a human opens in a browser, where a header cannot be set. It is a short-lived, single-session capability, not a reusable credential, and it only scopes reads/actions on that one connect session.
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
     * List a connect session's revisions
     * Every version of what the session asks for, oldest first.
     *
     * The review shows the current revision against today's access; this is the
     * history behind it, one entry per ask and amend. Gated like the review
     * read.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static listConnectSessionRevisions({
        sessionId,
        pollToken,
    }: {
        /**
         * ID of the connect session.
         */
        sessionId: string,
        /**
         * Opaque poll capability returned by :connect. Optional for the target agent's owner (with credentials:write and agents:write) and org:admin. Deliberately a query parameter: it rides the approval URL a human opens in a browser, where a header cannot be set. It is a short-lived, single-session capability, not a reusable credential, and it only scopes reads/actions on that one connect session.
         */
        pollToken?: (string | null),
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/connect-sessions/{session_id}/revisions',
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
     * Poll a connect session's status
     * Poll the lifecycle status of a connect session.
     *
     * Returns the session's current ``status`` and, once it has connected, the
     * resulting ``credential_id``, the account it ``connected_as``, and the
     * ``bound_scopes``; a failed session carries an ``error_code``. Agents and the
     * SPA poll this after ``:connect`` until the status is terminal.
     *
     * Auth is intentionally lightweight: the caller must be authenticated with
     * ``credentials:connect`` or ``credentials:write``, but the real capability
     * that scopes the response is the session's ``poll_token`` (or being the
     * target agent's owner / ``org:admin``). An agent handed the token by
     * ``:connect`` can poll without full session-read rights. A missing session,
     * a ``poll_token`` mismatch, and a caller who is neither all answer a uniform
     * 403, so the endpoint is not a session-id enumeration oracle.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static pollConnectSessionStatus({
        sessionId,
        pollToken,
    }: {
        /**
         * ID of the connect session.
         */
        sessionId: string,
        /**
         * Opaque poll capability returned by :connect. Optional for the target agent's owner (with credentials:write and agents:write) and org:admin. Deliberately a query parameter: it rides the approval URL a human opens in a browser, where a header cannot be set. It is a short-lived, single-session capability, not a reusable credential, and it only scopes reads/actions on that one connect session.
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
     * Amend an open connect session's request
     * Change what an open session asks for; the reviewer sees the new revision.
     *
     * The session's initiator, or for an edit session a human who may confirm
     * it (a reviewer editing the request; the revision records who made it),
     * while it is open (not yet confirmed or ended); a closed session is 409
     * `session_closed`. The fields sent replace
     * those parts of the ask and the rest stay. Edit sessions take `rules` and
     * `resume`, relative to the binding as it was when the edit opened; create
     * sessions take `requested_permission_rules` and `requested_scopes`, which
     * replace the ask outright; both take `reason`. The session id, approval
     * URL and poll token stay the same, and any confirm must name the new
     * `revision_id`.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static amendConnectSession({
        sessionId,
        requestBody,
    }: {
        /**
         * ID of the connect session.
         */
        sessionId: string,
        /**
         * Change an open session's ask; fields sent replace those parts, the rest stay.
         */
        requestBody: AmendSessionRequest,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/connect-sessions/{session_id}:amend',
            path: {
                'session_id': sessionId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                409: `The session is closed: confirmed or ended`,
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
        /**
         * ID of the connect session.
         */
        sessionId: string,
        /**
         * Opaque poll capability returned by :connect. Optional for the target agent's owner (with credentials:write and agents:write) and org:admin. Deliberately a query parameter: it rides the approval URL a human opens in a browser, where a header cannot be set. It is a short-lived, single-session capability, not a reusable credential, and it only scopes reads/actions on that one connect session.
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
     * holds (``existing_credential``), or an edit session's
     * final rules (``edit``, naming the reviewed ``revision_id``). Secrets are write-only:
     * they never appear in a response, audit entry or log.
     *
     * Shares the ``:connect`` per-actor rate bucket — this is the endpoint
     * that actually fires the vendor's device-authorization call, and a
     * failed ``begin`` leaves the session retryable, so it must not be
     * free to hammer during a vendor incident. Gated like the review read
     * (``poll_token`` or owner / ``org:admin``; 403 otherwise).
     *
     * A response carrying an ``authorize_url`` also sets a short-lived
     * ``HttpOnly`` cookie scoped to the OAuth callback path: the vendor
     * sign-in completes only in the approver's browser that called this
     * endpoint, whoever opened the session.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static confirmConnectSession({
        sessionId,
        requestBody,
        pollToken,
    }: {
        /**
         * ID of the connect session.
         */
        sessionId: string,
        /**
         * How to complete the session, selected by `kind`. A body without `kind` is the `oauth` variant.
         */
        requestBody: (OAuthConfirmSessionRequest | ApiKeyConfirmSessionRequest | BearerConfirmSessionRequest | BasicConfirmSessionRequest | OwnOAuthClientConfirmSessionRequest | ExistingCredentialConfirmSessionRequest | EditConfirmSessionRequest),
        /**
         * Opaque poll capability returned by :connect. Optional for the target agent's owner (with credentials:write and agents:write) and org:admin. Deliberately a query parameter: it rides the approval URL a human opens in a browser, where a header cannot be set. It is a short-lived, single-session capability, not a reusable credential, and it only scopes reads/actions on that one connect session.
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
        /**
         * ID of the connect session.
         */
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
     * callers: `agent_id` is optional and must name an agent the caller owns
     * (`org:admin` may name any agent); any other id is refused with 400
     * `connect_session_agent_not_found`, the same as an unknown agent. Naming
     * an owned agent also takes `agents:write`, which confirming for it needs
     * (403 `connect_session_confirmation_forbidden` otherwise), and an active
     * agent (409 `connect_session_agent_inactive` otherwise). When
     * named, confirm creates the direct agent-credential binding + permission
     * rules; when omitted, the credential connects unbound and an agent can
     * be bound later through the credentials API.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static integrationsConnect({
        requestBody,
    }: {
        /**
         * Start connecting a credential for a vendor or a registry API.
         */
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
    /**
     * Ask to change a credential binding
     * Open a connect session that edits an existing binding instead of creating one.
     *
     * The change (rules to add, remove or replace, and resuming a suspended
     * binding) is resolved against the binding as it is now, and a human
     * approves it on the review page; nothing changes until then. An agent
     * edits its own binding and must omit `agent_id`; a human names the agent
     * and must be its owner or an org admin. A change that is already in place
     * is 422 `nothing_to_change`, and a rule id the binding does not hold is 422
     * `unknown_rule`. A binding can have several open edits, each rebased as
     * others land; an agent asking again while its own is open is 409
     * `edit_open` with that session's `session_id`, to amend instead.
     * @returns any Successful Response
     * @throws ApiError
     */
    public static integrationsEditConnection({
        requestBody,
    }: {
        /**
         * Ask to change an existing credential binding; a human approves the change.
         */
        requestBody: EditConnectionRequest,
    }): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/integrations:edit-connection',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                400: `Bad Request`,
                401: `Unauthorized`,
                403: `Forbidden`,
                404: `No binding the caller may edit for this credential`,
                409: `The calling agent already has an open edit of this binding; amend it`,
                422: `Unprocessable Entity`,
                429: `Rate limit or open-session cap`,
                500: `Internal Server Error`,
                503: `Service Unavailable`,
            },
        });
    }
}
