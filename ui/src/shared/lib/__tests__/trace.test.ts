import { describe, it, expect } from 'vitest';
import { hasTrace } from '@/shared/lib';

describe('hasTrace', () => {
	it('accepts a real trace id', () => {
		expect(hasTrace('trace_abc')).toBe(true);
	});

	it.each([null, undefined, '', 'unknown'])('rejects %j: it opens no trace', (id) => {
		expect(hasTrace(id)).toBe(false);
	});
});
