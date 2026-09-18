// Pure API-identity keys.
//
// Framework-free (no React, no React Query) so the picker's selection set and
// the agents surface's add-APIs preflight key the SAME API to the same string.
//
// Identity is `vendor/name`, case- and whitespace-normalised. It deliberately
// excludes `version`: a spec revision is the same upstream API reached with the
// same account, so two versions must collapse to one pick and must both match
// the one credential that covers them. Including it would classify an obvious
// credential reuse as "needs a new credential".

/** Normalised `vendor/name` identity key. */
export function apiRefKey(ref: { vendor: string; name: string }): string {
	return `${ref.vendor.trim().toLowerCase()}/${ref.name.trim().toLowerCase()}`;
}
