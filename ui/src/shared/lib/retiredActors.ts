/**
 * Display helpers for the service-account actor type retired in theme 8.
 *
 * Every service account was migrated to a successor agent. Two traces remain
 * that read paths must still render, not drop or crash on:
 *
 * - Historical execution/audit/usage rows keep `actor_type = 'service_account'`
 *   and the raw `sva_…` id. There is no live entity to resolve or link to, so
 *   those rows get a "(retired service account)" label.
 * - Successor agents carry an immutable `registered_by` stamp that survives
 *   renames and identifies the lineage.
 */

/** `registered_by` stamp the theme-8 migration writes on every successor agent. */
export const SERVICE_ACCOUNT_SUCCESSOR_REGISTRAR = 'system:theme8-sa-migration';

/** Suffix that marks a historical service-account actor id as retired. */
export const RETIRED_SERVICE_ACCOUNT_SUFFIX = '(retired service account)';

/** Wire `actor_type` string historical service-account rows still carry. */
export const RETIRED_SERVICE_ACCOUNT_ACTOR_TYPE = 'service_account';

/** Label for a historical service-account actor id, e.g. `sva_1 (retired service account)`. */
export function retiredServiceAccountLabel(actorId: string): string {
	return `${actorId} ${RETIRED_SERVICE_ACCOUNT_SUFFIX}`;
}
