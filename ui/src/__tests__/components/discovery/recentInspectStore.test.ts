import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import {
	clearRecents,
	pushRecent,
	useRecentInspects,
} from '@/components/discovery/recentInspectStore';

const STORAGE_KEY = 'discover.recentInspect.v1';
const RING_CAP = 5;

describe('recentInspectStore', () => {
	beforeEach(() => {
		window.sessionStorage.clear();
		clearRecents();
	});

	afterEach(() => {
		window.sessionStorage.clear();
	});

	it('pushes entries to the front and stamps inspectedAt', () => {
		const before = Date.now();
		pushRecent({ apiId: 'stripe', name: 'Stripe', source: 'directory' });
		const stored = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) ?? '[]');
		expect(stored).toHaveLength(1);
		expect(stored[0].apiId).toBe('stripe');
		expect(stored[0].name).toBe('Stripe');
		expect(stored[0].source).toBe('directory');
		expect(stored[0].inspectedAt).toBeGreaterThanOrEqual(before);
	});

	it('dedupes by apiId — re-pushing moves the entry to the front', () => {
		pushRecent({ apiId: 'a', name: 'A' });
		pushRecent({ apiId: 'b', name: 'B' });
		pushRecent({ apiId: 'a', name: 'A (new name)' });
		const stored: { apiId: string; name?: string }[] = JSON.parse(
			window.sessionStorage.getItem(STORAGE_KEY) ?? '[]',
		);
		expect(stored.map((e) => e.apiId)).toEqual(['a', 'b']);
		expect(stored[0].name).toBe('A (new name)');
	});

	it('caps the ring buffer at RING_CAP entries', () => {
		for (let i = 0; i < RING_CAP + 3; i++) {
			pushRecent({ apiId: `api-${i}`, name: `API ${i}` });
		}
		const stored: { apiId: string }[] = JSON.parse(
			window.sessionStorage.getItem(STORAGE_KEY) ?? '[]',
		);
		expect(stored).toHaveLength(RING_CAP);
		// Most recent push first, oldest dropped.
		expect(stored[0].apiId).toBe(`api-${RING_CAP + 2}`);
		expect(stored.map((e) => e.apiId)).not.toContain('api-0');
	});

	it('survives a JSON round-trip via sessionStorage', () => {
		pushRecent({ apiId: 'github', name: 'GitHub', source: 'workspace' });
		pushRecent({ apiId: 'slack', name: 'Slack', source: 'directory' });
		const raw = window.sessionStorage.getItem(STORAGE_KEY);
		expect(raw).toBeTruthy();
		const parsed = JSON.parse(raw ?? '[]');
		expect(parsed).toHaveLength(2);
		expect(parsed[0].apiId).toBe('slack');
		expect(parsed[1].apiId).toBe('github');
	});

	it('ignores malformed sessionStorage payloads', () => {
		window.sessionStorage.setItem(STORAGE_KEY, '{not json');
		// Should not throw and should treat existing as empty.
		pushRecent({ apiId: 'fresh', name: 'Fresh' });
		const stored = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) ?? '[]');
		expect(stored).toHaveLength(1);
		expect(stored[0].apiId).toBe('fresh');
	});

	it('useRecentInspects re-renders when the ring is mutated', () => {
		const { result } = renderHook(() => useRecentInspects());
		expect(result.current.entries).toHaveLength(0);
		act(() => {
			result.current.push({ apiId: 'twilio', name: 'Twilio' });
		});
		expect(result.current.entries).toHaveLength(1);
		expect(result.current.entries[0].apiId).toBe('twilio');
	});

	it('clearRecents empties the ring', () => {
		pushRecent({ apiId: 'a' });
		pushRecent({ apiId: 'b' });
		clearRecents();
		const stored = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) ?? '[]');
		expect(stored).toHaveLength(0);
	});
});
