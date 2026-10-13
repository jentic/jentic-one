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
     * ``org:admin`` sees every entry.
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
        targetType?: (AuditTargetType | null),
        targetId?: (string | null),
        actorId?: (string | null),
        origin?: (string | null),
        since?: (string | null),
        until?: (string | null),
        cursor?: (string | null),
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
