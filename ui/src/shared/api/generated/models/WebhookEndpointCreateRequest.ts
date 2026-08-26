/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request body for creating a webhook endpoint.
 */
export type WebhookEndpointCreateRequest = {
    /**
     * Optional per-endpoint IP/CIDR allowlist. When non-empty, an otherwise-blocked (private/internal) pinned IP inside one of these CIDRs is permitted at send — best for stable internal targets. The cloud-metadata range is never re-opened, whatever is listed. Empty means only the operator-wide egress policy applies.
     */
    allowed_cidrs?: Array<string>;
    /**
     * Event types to subscribe to. Empty means all relayable types.
     */
    event_types?: Array<string>;
    name: string;
    /**
     * Required for notification endpoints: the URL signed events are POSTed to.
     */
    target_url?: (string | null);
};

