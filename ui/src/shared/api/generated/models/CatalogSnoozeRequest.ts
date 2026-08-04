/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Body for ``POST /catalog/{api_id}:snooze`` (C1, #925).
 *
 * ``snoozed_until`` is optional: omit or send ``null`` to mute-until-newer (the primary
 * per-API affordance — the badge re-lights only when a *newer* upstream digest lands);
 * provide an ISO-8601 timestamp for a time-boxed snooze that lapses at that instant.
 */
export type CatalogSnoozeRequest = {
    /**
     * Optional expiry for the snooze (ISO-8601). Null = mute until a newer upstream digest is observed.
     */
    snoozed_until?: (string | null);
};

