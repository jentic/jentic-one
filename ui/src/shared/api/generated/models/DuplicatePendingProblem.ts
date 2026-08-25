/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ErrorItem } from './ErrorItem';
/**
 * RFC 9457 Problem Details for a 409 on ``POST /access-requests``.
 *
 * Filing a request whose target already has a pending request is refused with
 * a 409 whose body carries two extension members on top of the standard
 * Problem Details shape, so a client can attach to the existing request rather
 * than re-file. These are emitted at runtime by the access-request error hook
 * (``control/web/errors.py``); this model documents them in the OpenAPI spec so
 * the generated SDK exposes a typed 409
 * (``FileAccessRequestHTTPResp.ApplicationproblemJSON409``) instead of forcing
 * callers to parse the raw body (ARCH-21 Step 0).
 */
export type DuplicatePendingProblem = {
    /**
     * Console URL to review/approve the existing pending request.
     */
    approve_url: string;
    /**
     * An optional provider-specific code for internal error taxonomy and observability correlation.
     */
    code?: (string | null);
    /**
     * A human-readable explanation specific to this occurrence of the problem. MUST be present. Provide actionable information where possible.
     */
    detail: string;
    /**
     * An array of granular error details. Use when multiple validation errors or field-level problems need to be surfaced in a single response.
     */
    errors?: (Array<ErrorItem> | null);
    /**
     * The id of the pending access request that already covers the conflicting target.
     */
    existing_request_id: string;
    /**
     * A URI reference identifying the specific occurrence of the problem. Typically the request path.
     */
    instance?: (string | null);
    /**
     * The HTTP status code for this occurrence of the problem.
     */
    status?: (number | null);
    /**
     * A short, human-readable summary of the problem type. Should not change between occurrences except for localisation purposes.
     */
    title?: (string | null);
    /**
     * A URI reference identifying the problem type. When set to 'about:blank', the title SHOULD be the standard HTTP status phrase. Use an IANA-registered type URI where one applies.
     */
    type?: string;
};

