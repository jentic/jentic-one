/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { GovernedHostsResponse } from '../models/GovernedHostsResponse';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class GovernedHostsService {
    /**
     * Get Governed Hosts
     * The caller's governed host set (toolkit-bound hosts) with an ETag digest.
     *
     * **Always self-scoped** — derived from the authenticated identity's own
     * toolkit bindings; there is no cross-actor or admin variant. Toolkits bind
     * to agents, so agent-scoped tokens (the OAuth agent-consent flow's output)
     * are the callers this endpoint serves — a plain user token yields an empty
     * set. The ``digest`` covers exactly the ``data`` list and is also emitted as
     * a strong ``ETag``, so integrators poll with ``If-None-Match: "<digest>"``
     * and get an empty ``304`` until their host set actually changes (the
     * change-poll seam that replaces ``GET /apis`` enumeration for interception
     * scoping). Poll at most once per minute; on any ``5xx`` retain the last
     * known set — never fall back to an empty (intercept-nothing) list.
     *
     * Responses are identity-scoped and marked ``Cache-Control: private,
     * no-store`` — a shared cache must never serve one actor's host set to
     * another.
     * @returns GovernedHostsResponse Successful Response
     * @throws ApiError
     */
    public static getGovernedHosts({
        ifNoneMatch,
    }: {
        /**
         * Change-poll precondition: the `ETag` from a previous response (quoted, `"<digest>"`; the bare digest is accepted as a compatibility form). When it still matches, the response is an empty `304`.
         */
        ifNoneMatch?: (string | null),
    }): CancelablePromise<GovernedHostsResponse> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/governed-hosts',
            headers: {
                'if-none-match': ifNoneMatch,
            },
            errors: {
                304: `The host set still matches the presented \`If-None-Match\` — empty body, \`ETag\` echoed.`,
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
