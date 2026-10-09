import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ago } from '@/modules/agents/lib/ago';

const NOW = Date.UTC(2026, 9, 7, 12, 0, 0);

describe('ago', () => {
	beforeEach(() => {
		vi.useFakeTimers();
		vi.setSystemTime(NOW);
	});
	afterEach(() => {
		vi.useRealTimers();
	});

	it('says "just now" under a second', () => {
		expect(ago(new Date(NOW).toISOString())).toBe('just now');
	});

	it('suffixes the compact age with "ago"', () => {
		expect(ago(new Date(NOW - 3 * 60_000).toISOString())).toBe('3m ago');
		expect(ago(new Date(NOW - 4 * 86_400_000).toISOString())).toBe('4d ago');
	});
});
