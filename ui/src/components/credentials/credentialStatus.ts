import type { CredentialStatus } from './StatusDot';
import type { CredentialOut } from '@/api/types';
import { timeAgo } from '@/lib/time';

export interface CredentialStatusInfo {
	tone: CredentialStatus;
	/** Short headline — also used as the StatusDot aria-label. */
	label: string;
	/** Optional secondary tooltip line. */
	detail?: string;
}

/**
 * Single source of truth for deriving a credential's health badge from its
 * `CredentialOut` shape. Used by every surface that renders a `StatusDot`
 * (the credentials grid, the workspace API-detail rows, the add/bind dialogs)
 * so they all speak the same status language and never diverge.
 *
 * Precedence (most authoritative first):
 *
 *  1. `healthy === false` — broker recorded a 401/403, a /test came back
 *     unauthorized, or a Pipedream grant was rejected. Authoritative `broken`.
 *  2. `healthy === true`  — broker saw a `< 400` call or /test passed.
 *     Authoritative `ok` (green).
 *  3. `last_used_at` set, `healthy` null — older positive signal from before
 *     health tracking; `ok`, phrased as "used" rather than "verified".
 *  4. Nothing at all — `neutral` "never used". The calm resting state, NOT a
 *     warning — deliberately distinct from the amber `unknown`, which is
 *     reserved for an answered-but-ambiguous /test probe.
 *
 * NB: never a network probe. Rendering N rows must not fan out N upstream GETs;
 * that's the Test-connection button's job.
 */
export function deriveCredentialStatus(
	cred: Pick<CredentialOut, 'auth_type' | 'healthy' | 'last_used_at' | 'health_checked_at'>,
): CredentialStatusInfo {
	const isPipedream = cred.auth_type === 'pipedream_oauth';
	const checked =
		cred.health_checked_at != null ? `checked ${timeAgo(cred.health_checked_at)}` : null;

	if (cred.healthy === false) {
		return {
			tone: 'broken',
			label: isPipedream ? 'Connection rejected' : 'Credential rejected',
			detail: isPipedream
				? `Upstream returned 401/403 — reconnect to restore${checked ? ` · ${checked}` : ''}.`
				: `Upstream returned 401/403 — the secret may be expired or revoked${
						checked ? ` · ${checked}` : ''
					}.`,
		};
	}

	if (cred.healthy === true) {
		return {
			tone: 'ok',
			label: 'Working',
			detail: `Upstream accepted this credential${checked ? ` · ${checked}` : ''}.`,
		};
	}

	if (cred.last_used_at) {
		return {
			tone: 'ok',
			label: 'In use',
			detail: `Last used ${timeAgo(cred.last_used_at)} with no errors.`,
		};
	}

	return {
		tone: 'neutral',
		label: 'Never used',
		detail: 'No calls yet. Run Test connection on the edit page to verify it works.',
	};
}
