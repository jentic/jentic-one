/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AuditListResponse } from '../models/AuditListResponse';
import type { AuditResponse } from '../models/AuditResponse';
import type { AuditTargetType } from '../models/AuditTargetType';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AuditService {
    /**
     * List Audit Entries
     * List audit entries with optional filters.
     *
     * Scoped to the caller: their own entries plus those made by agents they own.
     * Org admins see every entry.
     * @returns AuditListResponse Successful Response
     * @throws ApiError
     */
    public static listAuditEntries({
        targetType,
        targetId,
        actorId,
        origin,
        since,
        until,
        cursor,
        limit = 50,
    }: {
        /**
         * Only return entries for this target type; must be sent together with `target_id`.
         */
        targetType?: (AuditTargetType | null),
        /**
         * Only return entries for this target ID; must be sent together with `target_type`.
         */
        targetId?: (string | null),
        /**
         * Only return entries made by this actor.
         */
        actorId?: (string | null),
        /**
         * Only return entries initiated from this surface, e.g. `dashboard` or `cli`.
         */
        origin?: (string | null),
        /**
         * Only return entries that occurred at or after this time (RFC 3339).
         */
        since?: (string | null),
        /**
         * Only return entries that occurred at or before this time (RFC 3339).
         */
        until?: (string | null),
        /**
         * Opaque cursor from a previous page's `next_cursor`; omit for the first page.
         */
        cursor?: (string | null),
        /**
         * Maximum number of entries to return (1-200, default 50).
         */
        limit?: number,
    }): CancelablePromise<AuditListResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/audit',
            query: {
                'target_type': targetType,
                'target_id': targetId,
                'actor_id': actorId,
                'origin': origin,
                'since': since,
                'until': until,
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
     * Get Audit Entry
     * Get a single audit entry by ID.
     * @returns AuditResponse Successful Response
     * @throws ApiError
     */
    public static getAuditEntry({
        auditId,
    }: {
        /**
         * ID of the audit entry.
         */
        auditId: string,
    }): CancelablePromise<AuditResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/audit/{audit_id}',
            path: {
                'audit_id': auditId,
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
