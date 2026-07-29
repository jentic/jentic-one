/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Display info for the filer's human owner (labelling only, not authorization).
 *
 * Server-resolved from ``filer_owner_id`` (falling back to ``created_by``
 * when the former is null, mirroring what consumers render) so they don't
 * need ``users:read`` (or a roster fetch) just to label a row. Absent when
 * the id doesn't resolve to a user (service-account filers, purged rows) or
 * on mutation responses, which skip the enrichment.
 */
export type AccessRequestOwnerResponse = {
    /**
     * The owner's full name, when set on the profile.
     */
    display_name?: (string | null);
    /**
     * The owner's email address.
     */
    email: string;
    /**
     * The resolved owner's user id (filer_owner_id, or created_by when null).
     */
    id: string;
};

