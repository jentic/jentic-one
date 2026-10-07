import { describe, it, expect } from 'vitest';
import { newestFirst } from '@/shared/lib/newestFirst';

describe('newestFirst', () => {
	it('orders by createdAt descending, unparseable last, ties by the tiebreak', () => {
		const rows = [
			{ id: 'old', createdAt: '2026-01-01T00:00:00Z' },
			{ id: 'bad', createdAt: 'not a date' },
			{ id: 'new', createdAt: '2026-08-01T09:00:00Z' },
			{ id: 'b-tie', createdAt: '2026-03-02T09:00:00Z' },
			{ id: 'a-tie', createdAt: '2026-03-02T09:00:00Z' },
		];
		const sorted = [...rows].sort(newestFirst((a, b) => a.id.localeCompare(b.id)));
		expect(sorted.map((r) => r.id)).toEqual(['new', 'a-tie', 'b-tie', 'old', 'bad']);
	});
});
