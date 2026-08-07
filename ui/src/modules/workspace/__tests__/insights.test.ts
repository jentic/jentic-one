import { describe, it, expect } from 'vitest';
import {
	shortOverlayId,
	shortRevisionId,
	summarizeOverlayActions,
	overlayLifecycle,
	revisionOriginLabel,
	overlayForRevision,
	revisionChangeSummary,
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

	it('is confirmed when materialized but not serving (or not yet materialized)', () => {
		expect(
			overlayLifecycle(
				overlay({ status: 'confirmed', confirmedRevisionId: 'rev_other' }),
				'rev_live',
			),
		).toBe('confirmed');
		expect(overlayLifecycle(overlay({ status: 'confirmed' }), 'rev_live')).toBe('confirmed');
	});

	it('is rolled-back when deprecated and the superseded revision serves again', () => {
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

	it('is deprecated otherwise (e.g. superseded by a re-import)', () => {
		expect(
			overlayLifecycle(
				overlay({
					status: 'deprecated',
					confirmedRevisionId: 'rev_overlay',
					supersededRevisionId: 'rev_base',
				}),
				'rev_fresh_import',
			),
		).toBe('deprecated');
		expect(overlayLifecycle(overlay({ status: 'deprecated' }), 'rev_live')).toBe('deprecated');
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
				revision({ operationCount: 19 }),
				revision({ operationCount: 19 }),
			),
		).toBe('19 operations (unchanged)');
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
	});

	it('describes the most recent lifecycle event', () => {
		const line = describeLastChange([base, overlayRev], [rolledBack], 'rev_base');
		expect(line).toMatch(/^rolled back /);
	});

	it('returns null with no events', () => {
		expect(describeLastChange([], [], null)).toBeNull();
	});

	it('summarizes the serving state in one line', () => {
		const live = { ...base, isCurrent: true };
		const line = describeServingState([live, overlayRev], [rolledBack], 'rev_base');
		expect(line).toContain('Serving revision rev_base');
		expect(line).toContain('1 overlay (0 active)');
		expect(line).toMatch(/last change: rolled back /);
	});

	it('reports a missing live revision', () => {
		expect(describeServingState([base], [], null)).toContain('No live revision');
	});
});
