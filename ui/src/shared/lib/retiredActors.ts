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

/**
 * Confirm-dialog warning for rotating or revoking a successor agent's key while
 * it is still the retired service account's original key.
 */
export const MIGRATED_SERVICE_ACCOUNT_KEY_WARNING =
	"This agent replaced a retired service account and still authenticates with that account's original key. Once replaced, that key is gone for good and cannot be restored.";

/**
 * True when an agent still holds the key the theme-8 migration copied over from
 * its service account.
 *
 * The migration inserted the successor's credential row itself
 * (`created_by` = its system actor), and any later generate or revoke stamps
 * `rotated_at`. So an active, never-rotated, migration-created key is the
 * original service-account key. This reads the credential row
 * (`GET /agents/{id}/api-key`) instead of the key history, which only covers
 * the latest 50 agent audit rows and can miss an old rotation.
 */
export function holdsMigratedServiceAccountKey(input: {
	registeredBy: string | null | undefined;
	keyStatus: string | null | undefined;
	keyRotatedAt: string | null | undefined;
	keyCreatedBy: string | null | undefined;
}): boolean {
	return (
		input.registeredBy === SERVICE_ACCOUNT_SUCCESSOR_REGISTRAR &&
		input.keyStatus === 'active' &&
		input.keyRotatedAt == null &&
		input.keyCreatedBy === SERVICE_ACCOUNT_SUCCESSOR_REGISTRAR
	);
}
