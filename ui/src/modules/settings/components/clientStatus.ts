/**
 * Single-sourced OAuth-client approval-status vocabulary: `status → label +
 * Badge variant` for every surface in the module (roster, approval queue,
 * detail sheet), so the same state never renders two ways.
 *
 * This is the D7 admin-approval lifecycle (`pending | approved | denied`) —
 * a DIFFERENT closed vocabulary from the shared `ActorStatus`
 * (`ActorStatusBadge`), which is why it lives module-local rather than
 * extending the shared actor map: an OAuth client is not a platform actor.
 *
 * `active` is the ORTHOGONAL kill switch (an approved client can be
 * deactivated without touching its approval status), so "Inactive" is a
 * separate chip via {@link isInactiveChipVisible}, never a fourth status.
 */
import type { BadgeVariant } from '@/shared/ui';
import type { OAuthClient } from '@/modules/settings/api/hooks';

export type ApprovalStatus = 'pending' | 'approved' | 'denied';

export const APPROVAL_STATUSES: readonly ApprovalStatus[] = ['pending', 'approved', 'denied'];

export const APPROVAL_STATUS_LABEL: Record<ApprovalStatus, string> = {
	pending: 'Pending',
	approved: 'Approved',
	denied: 'Denied',
};

export const APPROVAL_STATUS_VARIANT: Record<ApprovalStatus, BadgeVariant> = {
	pending: 'pending',
	approved: 'success',
	denied: 'danger',
};

/** Normalise the wire string; unknown values read as `pending` (undecided). */
export function toApprovalStatus(value: string): ApprovalStatus {
	return (APPROVAL_STATUSES as readonly string[]).includes(value)
		? (value as ApprovalStatus)
		: 'pending';
}

/**
 * Whether the orthogonal "Inactive" chip shows next to the status badge.
 * Pending/denied rows are inactive BY CONSTRUCTION (D7), so the chip would be
 * noise there — it only carries signal on an approved-but-deactivated row
 * (the #1312 "zombie": matches neither queue filter, blocked at the gate).
 */
export function isInactiveChipVisible(client: OAuthClient): boolean {
	return !client.active && toApprovalStatus(client.approval_status) === 'approved';
}

// ---------------------------------------------------------------------------
// Roster status segments (the toolbar filter vocabulary)
// ---------------------------------------------------------------------------

/**
 * The roster's status-segment vocabulary. Not the raw approval statuses:
 * operators think in terms of "working fleet vs. needs attention", so the
 * segments partition on the JOINT (approval_status, active) state —
 * `inactive` is specifically the approved+deactivated zombies (#1312), while
 * pending/denied rows live under their decision states.
 */
export type ClientStatusFilter = 'all' | 'active' | 'pending' | 'denied' | 'inactive';

export const CLIENT_STATUS_FILTERS: readonly ClientStatusFilter[] = [
	'all',
	'active',
	'pending',
	'denied',
	'inactive',
];

export const CLIENT_STATUS_FILTER_LABEL: Record<ClientStatusFilter, string> = {
	all: 'All',
	active: 'Active',
	pending: 'Pending',
	denied: 'Denied',
	inactive: 'Inactive',
};

/** Which segment (other than `all`) a client row belongs to. */
export function clientStatusSegment(client: OAuthClient): Exclude<ClientStatusFilter, 'all'> {
	const status = toApprovalStatus(client.approval_status);
	if (status === 'pending') return 'pending';
	if (status === 'denied') return 'denied';
	return client.active ? 'active' : 'inactive';
}

/**
 * Whether a client may be reactivated from the roster. ONLY approved+inactive
 * rows: reactivating a denied row would PATCH `active=true` while
 * `approval_status=denied`, which the D7 gate still blocks — a success toast
 * over a still-bricked client. A denied client's recovery path is the
 * approval queue's Approve verb, which sets approved+active atomically.
 */
export function canReactivate(client: OAuthClient): boolean {
	return !client.active && toApprovalStatus(client.approval_status) === 'approved';
}

/** Confidential clients only — public (PKCE-only) clients have no secret. */
export function canRotateSecret(client: OAuthClient): boolean {
	return client.active && client.token_endpoint_auth_method !== 'none';
}

// ---------------------------------------------------------------------------
// Row/cell derivations shared by the roster, queue, and detail sheet
// ---------------------------------------------------------------------------

/**
 * Unique origins derived from the redirect URIs — the VERIFIABLE identity
 * signal (the #1264 authorize-page posture), as opposed to the
 * attacker-chosen client_name.
 *
 * Non-special schemes (`cursor://…`, `vscode://…` — exactly the native/MCP
 * client class this signal exists for) parse successfully under WHATWG but
 * with an OPAQUE origin that serialises as the literal string "null", so
 * `url.origin` alone would render "null" AND dedupe distinct custom-scheme
 * clients into one entry. Derive `scheme://host` ourselves in that case;
 * when the host is empty too (`myapp:/cb`), fall back to the raw URI rather
 * than disappearing.
 */
export function clientOrigins(client: Pick<OAuthClient, 'redirect_uris'>): string[] {
	const origins = client.redirect_uris.map((uri) => {
		try {
			const url = new URL(uri);
			if (url.origin !== 'null') return url.origin;
			return url.host ? `${url.protocol}//${url.host}` : uri;
		} catch {
			return uri;
		}
	});
	return [...new Set(origins)];
}
