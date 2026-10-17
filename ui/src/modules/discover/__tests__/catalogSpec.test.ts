import { describe, it, expect } from 'vitest';
import { versionLabel } from '@/modules/discover/lib/catalogSpec';

describe('versionLabel', () => {
	it('prefixes a bare version with "v" and leaves an existing prefix alone', () => {
		expect(versionLabel('2024-01-01')).toBe('v2024-01-01');
		expect(versionLabel('1.0.0')).toBe('v1.0.0');
		expect(versionLabel('v3')).toBe('v3');
	});
});
