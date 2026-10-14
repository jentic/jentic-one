/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { OverlayConfirmRequest } from '../models/OverlayConfirmRequest';
import type { OverlayListResponse } from '../models/OverlayListResponse';
import type { OverlayResponse } from '../models/OverlayResponse';
import type { OverlaySubmitRequest } from '../models/OverlaySubmitRequest';
import type { OverlayUpdateRequest } from '../models/OverlayUpdateRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class OverlaysService {
    /**
     * List an API's overlays
     * List the overlays submitted against an API, newest first.
     *
     * Each overlay gives its `status` — `pending` (awaiting review), `confirmed`
     * (applied to the served spec) or `deprecated` (retired) — and links to the
     * actions valid in that state. Filter with `status`. Results are
     * cursor-paginated: pass `next_cursor` as `cursor` while `has_more` is
     * true.
     * @returns OverlayListResponse Successful Response
     * @throws ApiError
     */
    public static listOverlays({
        vendor,
        name,
        version,
        cursor,
        limit = 50,
        status,
    }: {
        vendor: string,
        name: string,
        version: string,
        cursor?: (string | null),
        limit?: number,
        status?: (string | null),
    }): CancelablePromise<OverlayListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{vendor}/{name}/{version}/overlays',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
            },
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
     * Submit an OpenAPI overlay
     * Propose a change to an API's spec as an OpenAPI Overlay document.
     *
     * An overlay is a list of `actions` that patch the API's OpenAPI document —
     * for example correcting a description or adding a missing parameter.
     * Submitting stores it as `pending`; it changes nothing until an operator
     * confirms it (`:confirm`). Optionally pin it to a `target_revision_id` and
     * record a `contributed_by` label. The document must be a JSON object with
     * an `actions` array (at most 512 actions, 1 MB), else 422
     * (`invalid_overlay_document`). Requires `apis:write`. Returns the overlay.
     * @returns OverlayResponse Successful Response
     * @throws ApiError
     */
    public static submitOverlay({
        vendor,
        name,
        version,
        requestBody,
    }: {
        vendor: string,
        name: string,
        version: string,
        requestBody: OverlaySubmitRequest,
    }): CancelablePromise<OverlayResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/apis/{vendor}/{name}/{version}/overlays',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
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
     * Deprecate an overlay
     * Retire an overlay so it is no longer offered for confirmation.
     *
     * The overlay moves to `deprecated` but is kept, and remains readable for
     * history; nothing is deleted. Deprecating does not rewrite the served
     * spec: to undo a confirmed overlay's change, use `:rollback`. Requires
     * `apis:write`. Returns 204.
     * @returns void
     * @throws ApiError
     */
    public static deprecateOverlay({
        vendor,
        name,
        version,
        overlayId,
    }: {
        vendor: string,
        name: string,
        version: string,
        overlayId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/apis/{vendor}/{name}/{version}/overlays/{overlay_id}',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
                'overlay_id': overlayId,
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
     * Get Overlay
     * Retrieve a single overlay by ID.
     * @returns OverlayResponse Successful Response
     * @throws ApiError
     */
    public static getOverlay({
        vendor,
        name,
        version,
        overlayId,
    }: {
        vendor: string,
        name: string,
        version: string,
        overlayId: string,
    }): CancelablePromise<OverlayResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/apis/{vendor}/{name}/{version}/overlays/{overlay_id}',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
                'overlay_id': overlayId,
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
     * Edit an overlay
     * Change an overlay's `document` or `target_revision_id`.
     *
     * What the edit does depends on the overlay's state:
     *
     * - **Pending** (or confirmed but not yet applied): the stored overlay is
     * updated; the served spec is untouched. Requires `apis:write`.
     * - **Confirmed and live**: the edited document is re-applied over the
     * overlay's original base and the result replaces the API's served spec.
     * This also requires `overlays:confirm`, else 403
     * (`overlay_rematerialize_forbidden`). It is refused with 409 if the
     * overlay is no longer the live revision, or
     * (`overlay_rollback_target_missing`) if no clean base was recorded.
     *
     * Returns the updated overlay.
     * @returns OverlayResponse Successful Response
     * @throws ApiError
     */
    public static updateOverlay({
        vendor,
        name,
        version,
        overlayId,
        requestBody,
    }: {
        vendor: string,
        name: string,
        version: string,
        overlayId: string,
        requestBody: OverlayUpdateRequest,
    }): CancelablePromise<OverlayResponse> {
        return __request(OpenAPI, {
            method: 'PATCH',
            url: '/apis/{vendor}/{name}/{version}/overlays/{overlay_id}',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
                'overlay_id': overlayId,
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
     * Confirm an overlay and apply it to the served spec
     * Apply a pending overlay, producing a new current revision of the API.
     *
     * The base spec is re-imported with the overlay applied and the result is
     * promoted to the API's current revision, so it is what the API now serves.
     * The overlay becomes `confirmed`. Confirming an already-applied overlay is
     * a no-op that returns it unchanged; a deprecated overlay is refused with
     * 409 (`overlay_conflict`), and an overlay whose actions cannot be applied
     * with 409 (`overlay_apply_conflict`).
     *
     * Requires `overlays:confirm` (implied by `org:admin`): contributors submit
     * overlays with `apis:write`, and an operator reviews and confirms them.
     * Agents cannot grant themselves this permission. Returns the overlay.
     * @returns OverlayResponse Successful Response
     * @throws ApiError
     */
    public static confirmOverlay({
        vendor,
        name,
        version,
        overlayId,
        requestBody,
    }: {
        vendor: string,
        name: string,
        version: string,
        overlayId: string,
        requestBody: OverlayConfirmRequest,
    }): CancelablePromise<OverlayResponse> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/apis/{vendor}/{name}/{version}/overlays/{overlay_id}:confirm',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
                'overlay_id': overlayId,
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
     * Roll back a confirmed overlay
     * Undo a confirmed overlay by restoring the revision it replaced.
     *
     * The overlay's revision is archived and the revision it superseded becomes
     * the API's current revision again, so the served spec reverts, and the
     * overlay is marked `deprecated`. Requires `overlays:confirm`, like
     * confirming.
     *
     * Preconditions: the overlay is `confirmed`, its revision is still the
     * live one, and the revision it replaced is still restorable. Otherwise
     * nothing changes and 409 is returned: `overlay_conflict`,
     * `overlay_rollback_target_missing`, or `host_owned_by_other_vendor` when
     * another vendor's live API now serves one of the restored revision's
     * hosts. Returns 204.
     * @returns void
     * @throws ApiError
     */
    public static rollbackOverlay({
        vendor,
        name,
        version,
        overlayId,
    }: {
        vendor: string,
        name: string,
        version: string,
        overlayId: string,
    }): CancelablePromise<void> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/apis/{vendor}/{name}/{version}/overlays/{overlay_id}:rollback',
            path: {
                'vendor': vendor,
                'name': name,
                'version': version,
                'overlay_id': overlayId,
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
