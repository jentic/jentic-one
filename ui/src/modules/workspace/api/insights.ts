/**
 * insights — pure derivations that make revisions & overlays legible.
 *
 * The wire payloads are correct but opaque (KSUIDs, UUIDs, raw overlay JSON,
 * a bare `status` string). These helpers turn them into the human-readable
 * strings the Workspace detail page renders: unique short ids, overlay action
 * summaries, a lifecycle state distinct from origin, revision origin labels,
 * and the "current serving state" header line. Pure + unit-tested; no I/O.
 */
import { healthTier } from '@/shared/lib';
import type {
	ApiRevision,
	Overlay,
	RevisionState,
	UsageRow,
	WorkspaceApi,
} from '@/modules/workspace/api/types';

// ---------------------------------------------------------------------------
// Short ids
// ---------------------------------------------------------------------------

/**
 * A short-but-unique display form of a prefixed KSUID (e.g. an overlay id).
 *
 * KSUIDs are time-ordered: ids created close together share their LEADING
 * characters (two overlays submitted a minute apart both render `ovr_6a75`
 * under a naive `slice(0, 8)` — the id-collision bug). The TRAILING characters
 * are the random payload, so keep the prefix and the tail:
 * `ovr_6a75aa8e6edd9723f71840e8` → `ovr_…1840e8`.
 */
export function shortOverlayId(id: string): string {
	const sep = id.indexOf('_');
	const prefix = sep > 0 ? id.slice(0, sep + 1) : '';
	const payload = sep > 0 ? id.slice(sep + 1) : id;
	if (payload.length <= 8) return id;
	return `${prefix}…${payload.slice(-6)}`;
}

/**
 * A short display form of a revision UUID (first hex group). UUIDv4s are
 * random from the first character, so — unlike KSUIDs — the head is the
 * distinguishing part.
 */
export function shortRevisionId(id: string): string {
	return id.slice(0, 8);
}

// ---------------------------------------------------------------------------
// Dates
// ---------------------------------------------------------------------------

/** Coarse age of an instant (`just now`, `5m ago`, `3d ago`, `2y ago`); null if unparseable. */
export function formatAgo(iso: string): string | null {
	const ts = Date.parse(iso);
	if (Number.isNaN(ts)) return null;
	const sec = Math.round((Date.now() - ts) / 1000);
	if (sec < 60) return 'just now';
	const min = Math.round(sec / 60);
	if (min < 60) return `${min}m ago`;
	const hr = Math.round(min / 60);
	if (hr < 24) return `${hr}h ago`;
	const day = Math.round(hr / 24);
	if (day < 30) return `${day}d ago`;
	const mo = Math.round(day / 30);
	if (mo < 12) return `${mo}mo ago`;
	return `${Math.round(mo / 12)}y ago`;
}

/**
 * Compact absolute datetime (e.g. "7 Aug 2026, 10:52") — the one date format
 * for revision/overlay row metadata, so the two sections can't drift apart.
 */
export function formatDateTime(iso: string): string {
	const ts = Date.parse(iso);
	if (Number.isNaN(ts)) return iso;
	return new Date(ts).toLocaleString(undefined, {
		day: 'numeric',
		month: 'short',
		year: 'numeric',
		hour: '2-digit',
		minute: '2-digit',
	});
}

/** Compact absolute date, e.g. "Aug 7" (current year) or "Aug 7, 2025". */
function shortDate(iso: string): string {
	const ts = Date.parse(iso);
	if (Number.isNaN(ts)) return iso;
	const date = new Date(ts);
	const sameYear = date.getFullYear() === new Date().getFullYear();
	return date.toLocaleDateString(undefined, {
		month: 'short',
		day: 'numeric',
		...(sameYear ? {} : { year: 'numeric' }),
	});
}

// ---------------------------------------------------------------------------
// Overlay action summary
// ---------------------------------------------------------------------------

function asRecord(value: unknown): Record<string, unknown> | null {
	return value != null && typeof value === 'object' && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: null;
}

/** `$.paths['/pet'].get` → `paths['/pet'].get`; `$` → `document root`. */
function prettyTarget(target: unknown): string {
	if (typeof target !== 'string' || target === '' || target === '$') return 'document root';
	return target.replace(/^\$\.?/, '');
}

function clip(text: string, max: number): string {
	return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** Compact rendering of an update value: `servers (2 entries)`, `info.title = "…"`. */
function describeUpdate(target: string, value: unknown): string {
	if (Array.isArray(value)) {
		return `Replaced ${target} (${value.length} ${value.length === 1 ? 'entry' : 'entries'})`;
	}
	const record = asRecord(value);
	if (record) {
		const keys = Object.keys(record);
		if (keys.length === 0) return `Updated ${target} (no fields — empty object)`;
		const shown = keys.slice(0, 3).join(', ');
		const more = keys.length > 3 ? ', …' : '';
		return `Updated ${target} (${shown}${more})`;
	}
	return `Set ${target} = ${clip(JSON.stringify(value) ?? 'null', 40)}`;
}

/**
 * Human-readable, one-line-per-action summary of an OpenAPI Overlay document.
 *
 * Prefers the author's own `description` on each action (the
 * `contribute-spec-fix` skill writes one per action); falls back to a derived
 * `verb + target` line. An action with neither `remove` nor `update` is
 * rendered as unrecognized rather than silently dropped — the backend rejects
 * it at confirm, so hiding it would summarize a doomed overlay as empty.
 * Returns `[]` only for a malformed/absent document, so callers can degrade
 * to nothing rather than crash on contributor-controlled JSON.
 */
export function summarizeOverlayActions(document: unknown): string[] {
	const doc = asRecord(document);
	const actions = doc?.actions;
	if (!Array.isArray(actions)) return [];

	return actions.flatMap((raw): string[] => {
		const action = asRecord(raw);
		if (!action) return [];
		if (typeof action.description === 'string' && action.description.trim() !== '') {
			return [clip(action.description.trim(), 140)];
		}
		const target = prettyTarget(action.target);
		// Truthy check mirrors the backend applier's own `action.get("remove")`.
		if (action.remove) return [`Removed ${target}`];
		if (Object.prototype.hasOwnProperty.call(action, 'update')) {
			return [describeUpdate(target, action.update)];
		}
		return [`Unrecognized action on ${target} (no update or remove)`];
	});
}

// ---------------------------------------------------------------------------
// Overlay lifecycle
// ---------------------------------------------------------------------------

/**
 * The overlay lifecycle states the UI badges. Derived from the wire `status`
 * plus the persisted `deprecated_reason` and the API's current revision — the
 * wire `status` alone can't distinguish "serving right now" (active) from
 * "confirmed but superseded", nor "rolled back" from "deprecated by
 * re-import".
 */
export type OverlayLifecycle =
	| 'pending'
	| 'active'
	| 'superseded'
	| 'confirmed'
	| 'rolled-back'
	| 'deprecated-serving'
	| 'deprecated';

/**
 * Derive an overlay's lifecycle state.
 *
 * - `pending`            — submitted, awaiting confirm.
 * - `active`             — confirmed and its materialized revision is serving now.
 * - `superseded`         — materialized, but a newer revision has since taken
 *                          over (promote / re-import / newer overlay).
 * - `confirmed`          — confirmed but not (yet) materialized.
 * - `rolled-back`        — deprecated by a `:rollback` (durable via
 *                          `deprecated_reason`; legacy rows without a reason
 *                          fall back to "its superseded revision is live again").
 * - `deprecated-serving` — deprecated, but its materialized revision is STILL
 *                          the one serving (a manual deprecate doesn't touch
 *                          revisions) — the status label lags served reality.
 * - `deprecated`         — deprecated any other way (manual, or superseded by
 *                          a re-import adopting a fresh upstream spec).
 */
export function overlayLifecycle(
	overlay: Pick<
		Overlay,
		'status' | 'confirmedRevisionId' | 'supersededRevisionId' | 'deprecatedReason'
	>,
	currentRevisionId: string | null,
): OverlayLifecycle {
	if (overlay.status === 'pending') return 'pending';
	if (overlay.status === 'confirmed') {
		if (overlay.confirmedRevisionId === null) return 'confirmed';
		return overlay.confirmedRevisionId === currentRevisionId ? 'active' : 'superseded';
	}
	// Deprecated. Its revision still serving means nothing superseded it — the
	// deprecate was a status-only flip and the patched spec is still live.
	if (overlay.confirmedRevisionId !== null && overlay.confirmedRevisionId === currentRevisionId) {
		return 'deprecated-serving';
	}
	if (overlay.deprecatedReason === 'rollback') return 'rolled-back';
	// Legacy rows predate `deprecated_reason`; fall back to the rollback
	// signature ("the revision this overlay superseded is live again"). Only
	// trustworthy at rollback time — later transitions can drift it, which is
	// exactly why the reason is now persisted.
	if (
		overlay.deprecatedReason === null &&
		overlay.confirmedRevisionId !== null &&
		overlay.supersededRevisionId !== null &&
		overlay.supersededRevisionId === currentRevisionId
	) {
		return 'rolled-back';
	}
	return 'deprecated';
}

/** Human label for a lifecycle state. */
export const OVERLAY_LIFECYCLE_LABEL: Record<OverlayLifecycle, string> = {
	pending: 'pending',
	active: 'active',
	superseded: 'superseded',
	confirmed: 'confirmed',
	'rolled-back': 'rolled back',
	'deprecated-serving': 'deprecated · still serving',
	deprecated: 'deprecated',
};

/**
 * The dated note describing where the overlay is in its lifecycle. Falls back
 * to the submission date so a row never loses its only timestamp when a later
 * lifecycle field is missing.
 */
export function overlayLifecycleNote(
	overlay: Pick<
		Overlay,
		'createdAt' | 'confirmedAt' | 'deprecatedAt' | 'supersededRevisionId' | 'deprecatedReason'
	>,
	lifecycle: OverlayLifecycle,
): string {
	const submitted = `Submitted ${formatDateTime(overlay.createdAt)}`;
	switch (lifecycle) {
		case 'rolled-back':
			return overlay.deprecatedAt
				? `Rolled back ${formatDateTime(overlay.deprecatedAt)}${
						overlay.supersededRevisionId
							? ` — revision ${shortRevisionId(overlay.supersededRevisionId)} restored`
							: ''
					}`
				: submitted;
		case 'deprecated-serving':
			return overlay.deprecatedAt
				? `Deprecated ${formatDateTime(overlay.deprecatedAt)} — its revision is still serving; roll back or re-import to remove it`
				: submitted;
		case 'deprecated':
			// The persisted reason tells re-import supersession apart from a manual
			// retire; legacy rows (no reason) use the materialization signal.
			if (!overlay.deprecatedAt) return submitted;
			return overlay.deprecatedReason === 'superseded_by_reimport'
				? `Superseded by re-import ${formatDateTime(overlay.deprecatedAt)}`
				: overlay.deprecatedReason === 'manual'
					? `Deprecated ${formatDateTime(overlay.deprecatedAt)}`
					: `${overlay.confirmedAt ? 'Superseded' : 'Deprecated'} ${formatDateTime(overlay.deprecatedAt)}`;
		case 'active':
		case 'superseded':
		case 'confirmed':
			return overlay.confirmedAt
				? `Confirmed ${formatDateTime(overlay.confirmedAt)}`
				: submitted;
		case 'pending':
			return submitted;
	}
}

// ---------------------------------------------------------------------------
// Revision state & origin
// ---------------------------------------------------------------------------

/**
 * Human label for a revision's wire state — capitalized for display so rows
 * don't render the raw enum (`published` next to a capitalized `Live` badge).
 * Unknown future wire values fall back to the raw string rather than lying.
 */
export function revisionStateLabel(state: RevisionState): string {
	switch (state) {
		case 'imported':
			return 'Imported';
		case 'published':
			return 'Published';
		case 'draft':
			return 'Draft';
		case 'archived':
			return 'Archived';
		default:
			return state;
	}
}

/**
 * One-line origin for a revision row: `import` / `catalog import` / `upload`
 * / `overlay ovr_…1840e8` (when the producing overlay is known).
 */
export function revisionOriginLabel(
	revision: Pick<ApiRevision, 'origin' | 'sourceType'>,
	producedByOverlayId?: string | null,
): string {
	switch (revision.origin) {
		case 'overlay':
			return producedByOverlayId
				? `overlay ${shortOverlayId(producedByOverlayId)}`
				: 'overlay';
		case 'catalog':
			return 'catalog import';
		case 'uploaded':
			return 'upload';
		default:
			return revision.sourceType === 'url' ? 'import (url)' : 'import';
	}
}

/**
 * Find the overlay whose materialization produced `revisionId`, if any is in
 * the loaded overlay list (links revisions ⇄ overlays without a new endpoint).
 */
export function overlayForRevision(overlays: Overlay[], revisionId: string): Overlay | null {
	return overlays.find((o) => o.confirmedRevisionId === revisionId) ?? null;
}

/**
 * One-line "what changed" for a revision, vs. the revision created just
 * before it (list order is newest-first, so callers pass `revisions[i + 1]`).
 * Operation-count delta is the only change signal the list payload carries —
 * a zero delta says nothing about non-operation changes (servers, info,
 * auth…), so it reads "(same count)" rather than claiming "unchanged"; the
 * Diff view carries the full story.
 */
export function revisionChangeSummary(
	revision: Pick<ApiRevision, 'operationCount'>,
	previous: Pick<ApiRevision, 'operationCount'> | null,
): string {
	const count = `${revision.operationCount} operation${revision.operationCount === 1 ? '' : 's'}`;
	if (!previous) return count;
	const delta = revision.operationCount - previous.operationCount;
	if (delta === 0) return `${count} (same count)`;
	return `${count} (${delta > 0 ? '+' : ''}${delta} vs previous)`;
}

/**
 * The comparison base for a revision's diff: the revision created just before
 * it, matching the row's "what changed vs previous" summary (a diff button
 * next to a "vs previous" delta must not silently compare vs live). Null when
 * there is nothing to compare against (the API's first revision).
 */
export interface SpecDiffBase {
	/** Revision to diff FROM (`null` = the live revision). */
	revisionId: string | null;
	/** Short human label for the base, e.g. `previous · 24bf7e10`. */
	label: string;
}

export function diffBaseFor(
	revision: Pick<ApiRevision, 'revisionId'>,
	revisions: Pick<ApiRevision, 'revisionId'>[],
): SpecDiffBase | null {
	const index = revisions.findIndex((r) => r.revisionId === revision.revisionId);
	const previous = index >= 0 ? (revisions[index + 1] ?? null) : null;
	if (!previous) return null;
	return {
		revisionId: previous.revisionId,
		label: `previous · ${shortRevisionId(previous.revisionId)}`,
	};
}

// ---------------------------------------------------------------------------
// Current serving state header
// ---------------------------------------------------------------------------

export interface ChangeEvent {
	at: string;
	label: string;
	/**
	 * Who made the change, when the wire records it: the submitter of a
	 * revision's spec or of an overlay. Promotions, confirms and deprecations
	 * carry no actor on these rows (the audit log has them), so they're null
	 * rather than guessed.
	 */
	actorId: string | null;
}

/**
 * The most recent lifecycle event across revisions + overlays. Overlay
 * deprecations are labeled from the persisted `deprecated_reason` (durable — a
 * past rollback stays "rolled back" no matter what happened since), not
 * re-derived from the current revision pointer.
 */
export function lastChangeEvent(revisions: ApiRevision[], overlays: Overlay[]): ChangeEvent | null {
	const events: ChangeEvent[] = [];

	for (const rev of revisions) {
		// A plain upload creates a *draft* serving nothing — "imported" would
		// overstate it.
		const noun =
			rev.origin === 'overlay'
				? 'overlay applied'
				: rev.state === 'draft'
					? 'uploaded (draft)'
					: 'imported';
		events.push({ at: rev.createdAt, label: noun, actorId: rev.submittedBy });
		if (rev.promotedAt) events.push({ at: rev.promotedAt, label: 'promoted', actorId: null });
		if (rev.archivedAt) events.push({ at: rev.archivedAt, label: 'archived', actorId: null });
	}
	for (const overlay of overlays) {
		events.push({
			at: overlay.createdAt,
			label: 'overlay submitted',
			actorId: overlay.createdBy,
		});
		if (overlay.confirmedAt)
			events.push({ at: overlay.confirmedAt, label: 'overlay confirmed', actorId: null });
		if (overlay.deprecatedAt) {
			const label =
				overlay.deprecatedReason === 'rollback'
					? 'rolled back'
					: overlay.deprecatedReason === 'superseded_by_reimport'
						? 'superseded by re-import'
						: 'overlay deprecated';
			events.push({ at: overlay.deprecatedAt, label, actorId: null });
		}
	}

	const valid = events.filter((e) => !Number.isNaN(Date.parse(e.at)));
	if (valid.length === 0) return null;
	return valid.reduce((a, b) => (Date.parse(a.at) >= Date.parse(b.at) ? a : b));
}

/** {@link lastChangeEvent}, described (e.g. `rolled back Aug 7`). */
export function describeLastChange(revisions: ApiRevision[], overlays: Overlay[]): string | null {
	const latest = lastChangeEvent(revisions, overlays);
	return latest ? `${latest.label} ${shortDate(latest.at)}` : null;
}

/**
 * Who first brought the API into the workspace: the submitter of its oldest
 * revision. Null when that revision records no submitter (older rows, system
 * imports).
 */
export function importedBy(revisions: ApiRevision[]): string | null {
	if (revisions.length === 0) return null;
	const oldest = revisions.reduce((a, b) =>
		Date.parse(a.createdAt) <= Date.parse(b.createdAt) ? a : b,
	);
	return oldest.submittedBy;
}

/** Overlays submitted but not yet confirmed — someone has to review them. */
export function pendingOverlayCount(overlays: Overlay[]): number {
	return overlays.filter((o) => o.status === 'pending').length;
}

// ---------------------------------------------------------------------------
// Workspace list: what needs someone's attention
// ---------------------------------------------------------------------------

/**
 * Why an API in a shared workspace needs someone to look at it, most urgent
 * first. Each reason is something an operator can act on from the API's
 * page; plain facts (operation counts, revision counts) are not reasons.
 */
export type ApiAttention = 'failing' | 'no-credential' | 'draft' | 'update';

export const API_ATTENTION_LABEL: Record<ApiAttention, string> = {
	failing: 'Calls failing',
	'no-credential': 'No credential',
	draft: 'Not serving',
	update: 'Update available',
};

export function apiAttention(
	api: Pick<WorkspaceApi, 'currentRevisionId' | 'updateAvailable' | 'securitySchemes'>,
	context: {
		/** Credentials targeting the API; undefined while unknown. */
		credentialCount: number | undefined;
		/** Calls over the usage window; null = none, undefined = unknown. */
		traffic: UsageRow | null | undefined;
	},
): ApiAttention[] {
	const reasons: ApiAttention[] = [];
	const { traffic, credentialCount } = context;
	if (traffic && traffic.total > 0) {
		const successRate = ((traffic.total - traffic.failed) / traffic.total) * 100;
		if (healthTier(successRate) === 'failing') reasons.push('failing');
	}
	// Only an API that declares a security scheme needs a credential to be
	// callable; an open API with none is fine.
	if (api.securitySchemes.length > 0 && credentialCount === 0) reasons.push('no-credential');
	if (api.currentRevisionId === null) reasons.push('draft');
	if (api.updateAvailable) reasons.push('update');
	return reasons;
}

/**
 * The single "current serving state" line above the Revisions/Overlays
 * sections, e.g.:
 * `Serving revision dc9bcdeb (imported) · 2 overlays (0 active) · last change: rolled back Aug 7`.
 *
 * "Current" is derived from the revisions list alone (`isCurrent`) — one
 * source of truth for the whole line, so the serving fragment and the active
 * count can't disagree while separate query caches refetch. Callers must pass
 * COMPLETE lists (the hooks' background page walk owns that).
 */
export function describeServingState(revisions: ApiRevision[], overlays: Overlay[]): string {
	const live = revisions.find((r) => r.isCurrent) ?? null;
	const currentRevisionId = live?.revisionId ?? null;
	const serving = live
		? `Serving revision ${shortRevisionId(live.revisionId)} (${revisionOriginLabel(
				live,
				overlayForRevision(overlays, live.revisionId)?.id ?? null,
			)})`
		: 'No live revision';

	const active = overlays.filter(
		(o) => overlayLifecycle(o, currentRevisionId) === 'active',
	).length;
	const overlaysPart = `${overlays.length} overlay${overlays.length === 1 ? '' : 's'} (${active} active)`;

	const lastChange = describeLastChange(revisions, overlays);
	return [serving, overlaysPart, lastChange ? `last change: ${lastChange}` : null]
		.filter(Boolean)
		.join(' · ');
}

// ---------------------------------------------------------------------------
// Access tab: who is calling
// ---------------------------------------------------------------------------

export interface UsageCaller {
	actorType: string;
	actorId: string;
}

/**
 * Split a `group_by=agent` usage key — `actor_type/actor_id`, e.g.
 * `agent/agnt_…` or `user/usr_…` — into its parts. Null for the
 * unattributed bucket (an empty or malformed key), which has no one to name.
 */
export function parseUsageCaller(key: string | null | undefined): UsageCaller | null {
	if (!key) return null;
	const slash = key.indexOf('/');
	if (slash <= 0) return null;
	const actorId = key.slice(slash + 1);
	if (!actorId) return null;
	return { actorType: key.slice(0, slash), actorId };
}
