import { describe, it, expect } from 'vitest';
import {
	shortOverlayId,
	shortRevisionId,
	summarizeOverlayActions,
	overlayLifecycle,
	overlayLifecycleNote,
	revisionStateLabel,
	revisionOriginLabel,
	overlayForRevision,
	revisionChangeSummary,
	diffBaseFor,
	describeLastChange,
	describeServingState,
} from '@/modules/workspace/api/insights';
import type { ApiRevision, Overlay } from '@/modules/workspace/api/types';

function overlay(overrides: Partial<Overlay> = {}): Overlay {
	return {
		id: 'ovr_6a75aa8e6edd9723f71840e8',
		status: 'pending',
		createdBy: 'usr_1',
		contributedBy: null,
		document: null,
		createdAt: '2026-08-07T09:51:10Z',
		confirmedAt: null,
		deprecatedAt: null,
		deprecatedReason: null,
		targetRevisionId: null,
		confirmedRevisionId: null,
		supersededRevisionId: null,
		confirmHref: null,
		rollbackHref: null,
		deprecateHref: null,
		...overrides,
	};
}

function revision(overrides: Partial<ApiRevision> = {}): ApiRevision {
	return {
		revisionId: 'dc9bcdeb-f652-45eb-b7cb-d56e76a5d8aa',
		api: { vendor: 'a', name: 'b', version: '1', host: null },
		sourceType: 'inline',
		sourceUrl: null,
		specDigest: 'digest',
		operationCount: 19,
		state: 'imported',
		origin: null,
		submittedBy: null,
		isCurrent: false,
		promotedAt: null,
		archivedAt: null,
		createdAt: '2026-08-07T09:40:39Z',
		promoteHref: null,
		archiveHref: null,
		...overrides,
	};
}

describe('shortOverlayId', () => {
	it('keeps the prefix and the distinguishing KSUID tail', () => {
		// The two demo overlays that collided as "ovr_6a75" under slice(0, 8):
		const a = shortOverlayId('ovr_6a75aa8e6edd9723f71840e8');
		const b = shortOverlayId('ovr_6a75aaf71c1c073e38ab429c');
		expect(a).toBe('ovr_…1840e8');
		expect(b).toBe('ovr_…ab429c');
		expect(a).not.toBe(b);
	});

	it('returns short ids unchanged', () => {
		expect(shortOverlayId('ovr_abc')).toBe('ovr_abc');
		expect(shortOverlayId('noprefix')).toBe('noprefix');
	});
});

describe('shortRevisionId', () => {
	it('keeps the leading hex group of a UUID', () => {
		expect(shortRevisionId('dc9bcdeb-f652-45eb-b7cb-d56e76a5d8aa')).toBe('dc9bcdeb');
	});
});

describe('summarizeOverlayActions', () => {
	it('prefers the author-provided action descriptions', () => {
		const lines = summarizeOverlayActions({
			overlay: '1.0.0',
			actions: [
				{
					description: 'Remove the US-only servers block.',
					target: '$.servers',
					remove: true,
				},
				{
					description: 'Add a region-templated server (EU default, US selectable).',
					target: '$',
					update: { servers: [{ url: 'https://{region}.petstore.demo/api/v3' }] },
				},
			],
		});
		expect(lines).toEqual([
			'Remove the US-only servers block.',
			'Add a region-templated server (EU default, US selectable).',
		]);
	});

	it('derives verb + target when no description is present', () => {
		expect(
			summarizeOverlayActions({
				actions: [
					{ target: '$.servers', remove: true },
					{ target: '$.servers', update: [{ url: 'https://eu' }, { url: 'https://us' }] },
					{ target: '$.info', update: { title: 'New', version: '2' } },
					{ target: '$.info.version', update: '2.0.0' },
				],
			}),
		).toEqual([
			'Removed servers',
			'Replaced servers (2 entries)',
			'Updated info (title, version)',
			'Set info.version = "2.0.0"',
		]);
	});

	it('calls out empty-object updates instead of rendering empty parens', () => {
		expect(summarizeOverlayActions({ actions: [{ target: '$.info', update: {} }] })).toEqual([
			'Updated info (no fields — empty object)',
		]);
	});

	it('flags an action with neither update nor remove instead of hiding it', () => {
		// The backend rejects such an action at confirm — an empty summary would
		// present a doomed overlay as a no-op.
		expect(summarizeOverlayActions({ actions: [{ target: '$.servers' }] })).toEqual([
			'Unrecognized action on servers (no update or remove)',
		]);
	});

	it('returns [] for malformed or absent documents', () => {
		expect(summarizeOverlayActions(null)).toEqual([]);
		expect(summarizeOverlayActions('nope')).toEqual([]);
		expect(summarizeOverlayActions({ actions: 'nope' })).toEqual([]);
		expect(summarizeOverlayActions({ actions: [null, 42] })).toEqual([]);
	});
});

describe('overlayLifecycle', () => {
	it('is pending for a pending overlay', () => {
		expect(overlayLifecycle(overlay({ status: 'pending' }), 'rev_live')).toBe('pending');
	});

	it('is active when the materialized revision is serving', () => {
		expect(
			overlayLifecycle(
				overlay({ status: 'confirmed', confirmedRevisionId: 'rev_live' }),
				'rev_live',
			),
		).toBe('active');
	});

	it('is superseded when materialized but a newer revision took over', () => {
		expect(
			overlayLifecycle(
				overlay({ status: 'confirmed', confirmedRevisionId: 'rev_other' }),
				'rev_live',
			),
		).toBe('superseded');
	});

	it('is confirmed when not yet materialized', () => {
		expect(overlayLifecycle(overlay({ status: 'confirmed' }), 'rev_live')).toBe('confirmed');
	});

	it('is rolled-back via the persisted reason (durable across later transitions)', () => {
		const rolledBack = overlay({
			status: 'deprecated',
			confirmedRevisionId: 'rev_overlay',
			supersededRevisionId: 'rev_base',
			deprecatedReason: 'rollback',
		});
		expect(overlayLifecycle(rolledBack, 'rev_base')).toBe('rolled-back');
		// A later confirm/import moved current on — the historical rollback verb
		// must NOT drift into "deprecated" (the pre-reason derivation did).
		expect(overlayLifecycle(rolledBack, 'rev_even_newer')).toBe('rolled-back');
	});

	it('falls back to the rollback signature for legacy rows without a reason', () => {
		expect(
			overlayLifecycle(
				overlay({
					status: 'deprecated',
					confirmedRevisionId: 'rev_overlay',
					supersededRevisionId: 'rev_base',
				}),
				'rev_base',
			),
		).toBe('rolled-back');
	});

	it('is deprecated-serving when a deprecated overlay still serves', () => {
		// A manual deprecate does not touch revisions — the overlay's patched
		// spec is still what the platform serves, and saying "deprecated" alone
		// would imply the fix is no longer applied.
		expect(
			overlayLifecycle(
				overlay({
					status: 'deprecated',
					confirmedRevisionId: 'rev_overlay',
					deprecatedReason: 'manual',
				}),
				'rev_overlay',
			),
		).toBe('deprecated-serving');
	});

	it('is deprecated otherwise (manual retire, or superseded by a re-import)', () => {
		expect(
			overlayLifecycle(
				overlay({
					status: 'deprecated',
					confirmedRevisionId: 'rev_overlay',
					supersededRevisionId: 'rev_base',
					deprecatedReason: 'superseded_by_reimport',
				}),
				'rev_fresh_import',
			),
		).toBe('deprecated');
		expect(overlayLifecycle(overlay({ status: 'deprecated' }), 'rev_live')).toBe('deprecated');
	});
});

describe('overlayLifecycleNote', () => {
	it('names re-import supersession from the persisted reason', () => {
		const o = overlay({
			status: 'deprecated',
			confirmedRevisionId: 'rev_overlay',
			deprecatedAt: '2026-08-07T10:00:00Z',
			deprecatedReason: 'superseded_by_reimport',
		});
		expect(overlayLifecycleNote(o, 'deprecated')).toMatch(/^Superseded by re-import /);
	});

	it('explains a deprecated-but-still-serving overlay', () => {
		const o = overlay({
			status: 'deprecated',
			confirmedRevisionId: 'rev_overlay',
			deprecatedAt: '2026-08-07T10:00:00Z',
			deprecatedReason: 'manual',
		});
		expect(overlayLifecycleNote(o, 'deprecated-serving')).toMatch(/still serving/);
	});

	it('falls back to the submission date so a row never loses its timestamp', () => {
		const o = overlay({ status: 'confirmed', confirmedAt: null });
		expect(overlayLifecycleNote(o, 'active')).toMatch(/^Submitted /);
	});

	it('notes the restored revision on a rollback', () => {
		const o = overlay({
			status: 'deprecated',
			supersededRevisionId: 'rev_base_1234',
			deprecatedAt: '2026-08-07T10:00:00Z',
			deprecatedReason: 'rollback',
		});
		expect(overlayLifecycleNote(o, 'rolled-back')).toMatch(/revision rev_base restored/);
	});
});

describe('revisionStateLabel', () => {
	it('capitalizes known wire states and passes unknown ones through', () => {
		expect(revisionStateLabel('imported')).toBe('Imported');
		expect(revisionStateLabel('published')).toBe('Published');
		expect(revisionStateLabel('draft')).toBe('Draft');
		expect(revisionStateLabel('archived')).toBe('Archived');
		expect(revisionStateLabel('future-state')).toBe('future-state');
	});
});

describe('revisionOriginLabel', () => {
	it('labels each origin', () => {
		expect(revisionOriginLabel(revision({ origin: 'catalog' }), null)).toBe('catalog import');
		expect(revisionOriginLabel(revision({ origin: 'uploaded' }), null)).toBe('upload');
		expect(revisionOriginLabel(revision({ origin: null, sourceType: 'url' }), null)).toBe(
			'import (url)',
		);
		expect(revisionOriginLabel(revision({ origin: null, sourceType: 'inline' }), null)).toBe(
			'import',
		);
	});

	it('names the producing overlay when known', () => {
		expect(revisionOriginLabel(revision({ origin: 'overlay' }), null)).toBe('overlay');
		expect(
			revisionOriginLabel(revision({ origin: 'overlay' }), 'ovr_6a75aa8e6edd9723f71840e8'),
		).toBe('overlay ovr_…1840e8');
	});
});

describe('overlayForRevision', () => {
	it('matches an overlay by its confirmed revision', () => {
		const target = overlay({ id: 'ovr_match', confirmedRevisionId: 'rev_1' });
		expect(overlayForRevision([overlay(), target], 'rev_1')).toBe(target);
		expect(overlayForRevision([overlay()], 'rev_1')).toBeNull();
	});
});

describe('revisionChangeSummary', () => {
	it('reports the operation-count delta vs the previous revision', () => {
		expect(revisionChangeSummary(revision({ operationCount: 19 }), null)).toBe('19 operations');
		expect(
			revisionChangeSummary(
				revision({ operationCount: 21 }),
				revision({ operationCount: 19 }),
			),
		).toBe('21 operations (+2 vs previous)');
		expect(
			revisionChangeSummary(revision({ operationCount: 1 }), revision({ operationCount: 3 })),
		).toBe('1 operation (-2 vs previous)');
	});

	it('says "same count", not "unchanged" — overlays often change non-operation sections', () => {
		expect(
			revisionChangeSummary(
				revision({ operationCount: 19 }),
				revision({ operationCount: 19 }),
			),
		).toBe('19 operations (same count)');
	});
});

describe('diffBaseFor', () => {
	const newest = revision({ revisionId: 'rev_c' });
	const middle = revision({ revisionId: 'rev_b' });
	const oldest = revision({ revisionId: 'rev_a' });
	const list = [newest, middle, oldest];

	it('diffs every row against the revision created just before it', () => {
		// Matches the row summary's "vs previous" delta — the diff button and the
		// one-liner must describe the same comparison.
		expect(diffBaseFor(newest, list)).toEqual({
			revisionId: 'rev_b',
			label: 'previous · rev_b',
		});
		expect(diffBaseFor(middle, list)).toEqual({
			revisionId: 'rev_a',
			label: 'previous · rev_a',
		});
	});

	it('returns null for the first-ever revision (nothing to compare)', () => {
		expect(diffBaseFor(oldest, list)).toBeNull();
		expect(diffBaseFor(newest, [newest])).toBeNull();
	});
});

describe('describeLastChange / describeServingState', () => {
	const base = revision({
		revisionId: 'rev_base',
		createdAt: '2026-08-07T09:40:00Z',
	});
	const overlayRev = revision({
		revisionId: 'rev_overlay',
		origin: 'overlay',
		state: 'archived',
		createdAt: '2026-08-07T09:52:00Z',
		archivedAt: '2026-08-07T09:55:00Z',
	});
	const rolledBack = overlay({
		id: 'ovr_6a75aa8e6edd9723f71840e8',
		status: 'deprecated',
		confirmedRevisionId: 'rev_overlay',
		supersededRevisionId: 'rev_base',
		createdAt: '2026-08-07T09:51:00Z',
		confirmedAt: '2026-08-07T09:52:00Z',
		deprecatedAt: '2026-08-07T09:56:00Z',
		deprecatedReason: 'rollback',
	});

	it('describes the most recent lifecycle event', () => {
		const line = describeLastChange([base, overlayRev], [rolledBack]);
		expect(line).toMatch(/^rolled back /);
	});

	it('keeps the historical verb durable — no drift when current moves on', () => {
		// Same events, but a later import made a different revision current;
		// the Aug 7 rollback must still read "rolled back".
		const line = describeLastChange([base, overlayRev], [rolledBack]);
		expect(line).toMatch(/^rolled back /);
	});

	it('labels re-import supersession from the persisted reason', () => {
		const superseded = overlay({
			...rolledBack,
			deprecatedReason: 'superseded_by_reimport',
		});
		expect(describeLastChange([], [superseded])).toMatch(/^superseded by re-import /);
	});

	it('labels a draft creation as an upload, not an import', () => {
		const draft = revision({ state: 'draft', createdAt: '2026-08-07T09:40:00Z' });
		expect(describeLastChange([draft], [])).toMatch(/^uploaded \(draft\) /);
	});

	it('returns null with no events', () => {
		expect(describeLastChange([], [])).toBeNull();
	});

	it('summarizes the serving state in one line, from the revisions list alone', () => {
		const live = { ...base, isCurrent: true };
		const line = describeServingState([live, overlayRev], [rolledBack]);
		expect(line).toContain('Serving revision rev_base');
		expect(line).toContain('1 overlay (0 active)');
		expect(line).toMatch(/last change: rolled back /);
	});

	it('counts an active overlay against the live revision it derives itself', () => {
		const live = revision({ revisionId: 'rev_overlay', isCurrent: true, origin: 'overlay' });
		const active = overlay({
			status: 'confirmed',
			confirmedRevisionId: 'rev_overlay',
		});
		const line = describeServingState([live, base], [active]);
		expect(line).toContain('(1 active)');
		expect(line).toContain('overlay ovr_…1840e8');
	});

	it('reports a missing live revision', () => {
		expect(describeServingState([base], [])).toContain('No live revision');
	});
});
