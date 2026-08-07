/**
 * insights — pure derivations that make revisions & overlays legible.
 *
 * The wire payloads are correct but opaque (KSUIDs, UUIDs, raw overlay JSON,
 * a bare `status` string). These helpers turn them into the human-readable
 * strings the Workspace detail page renders: unique short ids, overlay action
 * summaries, a lifecycle state distinct from origin, revision origin labels,
 * and the "current serving state" header line. Pure + unit-tested; no I/O.
 */
import type { ApiRevision, Overlay } from '@/modules/workspace/api/types';

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
 * `verb + target` line. Returns `[]` for a malformed/absent document so
 * callers can degrade to nothing rather than crash on contributor-controlled
 * JSON.
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
		if (action.remove) return [`Removed ${target}`];
		if ('update' in action) return [describeUpdate(target, action.update)];
		return [];
	});
}

// ---------------------------------------------------------------------------
// Overlay lifecycle
// ---------------------------------------------------------------------------

/**
 * The overlay lifecycle states the UI badges. Derived from the wire `status`
 * plus the API's current revision — the wire alone can't distinguish "serving
 * right now" (active) from "confirmed but superseded", nor "rolled back" from
 * "deprecated by re-import".
 */
export type OverlayLifecycle = 'pending' | 'active' | 'confirmed' | 'rolled-back' | 'deprecated';

/**
 * Derive an overlay's lifecycle state.
 *
 * - `active`      — confirmed and its materialized revision is serving now.
 * - `confirmed`   — confirmed, but its revision is not (or not yet) current.
 * - `rolled-back` — deprecated AND the revision it superseded is serving
 *                   again (the signature a `:rollback` leaves behind).
 * - `deprecated`  — deprecated any other way (manual, or superseded by a
 *                   re-import that adopted a fresh upstream spec).
 */
export function overlayLifecycle(
	overlay: Pick<
		Overlay,
		'status' | 'confirmedRevisionId' | 'supersededRevisionId' | 'confirmedAt'
	>,
	currentRevisionId: string | null,
): OverlayLifecycle {
	if (overlay.status === 'pending') return 'pending';
	if (overlay.status === 'confirmed') {
		return overlay.confirmedRevisionId !== null &&
			overlay.confirmedRevisionId === currentRevisionId
			? 'active'
			: 'confirmed';
	}
	// Deprecated: a rollback restores the superseded revision to current and
	// deprecates the overlay in the same transaction — so "the revision this
	// overlay superseded is live again" identifies a rollback.
	if (
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
	confirmed: 'confirmed',
	'rolled-back': 'rolled back',
	deprecated: 'deprecated',
};

// ---------------------------------------------------------------------------
// Revision origin
// ---------------------------------------------------------------------------

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
 * Operation-count delta is the only change signal the list payload carries;
 * the Diff view carries the full story.
 */
export function revisionChangeSummary(
	revision: Pick<ApiRevision, 'operationCount'>,
	previous: Pick<ApiRevision, 'operationCount'> | null,
): string {
	const count = `${revision.operationCount} operation${revision.operationCount === 1 ? '' : 's'}`;
	if (!previous) return count;
	const delta = revision.operationCount - previous.operationCount;
	if (delta === 0) return `${count} (unchanged)`;
	return `${count} (${delta > 0 ? '+' : ''}${delta} vs previous)`;
}

// ---------------------------------------------------------------------------
// Current serving state header
// ---------------------------------------------------------------------------

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

/** The most recent lifecycle event across revisions + overlays, described. */
export function describeLastChange(
	revisions: ApiRevision[],
	overlays: Overlay[],
	currentRevisionId: string | null,
): string | null {
	const events: { at: string; label: string }[] = [];

	for (const rev of revisions) {
		const noun = rev.origin === 'overlay' ? 'overlay applied' : 'imported';
		events.push({ at: rev.createdAt, label: noun });
		if (rev.promotedAt) events.push({ at: rev.promotedAt, label: 'promoted' });
		if (rev.archivedAt) events.push({ at: rev.archivedAt, label: 'archived' });
	}
	for (const overlay of overlays) {
		events.push({ at: overlay.createdAt, label: 'overlay submitted' });
		if (overlay.confirmedAt)
			events.push({ at: overlay.confirmedAt, label: 'overlay confirmed' });
		if (overlay.deprecatedAt) {
			const lifecycle = overlayLifecycle(overlay, currentRevisionId);
			events.push({
				at: overlay.deprecatedAt,
				label: lifecycle === 'rolled-back' ? 'rolled back' : 'overlay deprecated',
			});
		}
	}

	const valid = events.filter((e) => !Number.isNaN(Date.parse(e.at)));
	if (valid.length === 0) return null;
	const latest = valid.reduce((a, b) => (Date.parse(a.at) >= Date.parse(b.at) ? a : b));
	return `${latest.label} ${shortDate(latest.at)}`;
}

/**
 * The single "current serving state" line above the Revisions/Overlays
 * sections, e.g.:
 * `Serving revision dc9bcdeb (imported) · 2 overlays (0 active) · last change: rolled back Aug 7`.
 */
export function describeServingState(
	revisions: ApiRevision[],
	overlays: Overlay[],
	currentRevisionId: string | null,
): string {
	const live = revisions.find((r) => r.isCurrent) ?? null;
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

	const lastChange = describeLastChange(revisions, overlays, currentRevisionId);
	return [serving, overlaysPart, lastChange ? `last change: ${lastChange}` : null]
		.filter(Boolean)
		.join(' · ');
}
