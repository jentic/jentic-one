import { describe, expect, it } from 'vitest';
import { resolveReturnTo } from '@/shared/auth/returnTo';

describe('resolveReturnTo', () => {
	it('keeps the path, query and hash of an in-app location', () => {
		expect(
			resolveReturnTo({ pathname: '/agents', search: '?approve=cs_1&x=2', hash: '#top' }),
		).toBe('/agents?approve=cs_1&x=2#top');
	});

	it('returns a bare path when there is no query or hash', () => {
		expect(resolveReturnTo({ pathname: '/settings' })).toBe('/settings');
	});

	it('falls back to the app home without a remembered location', () => {
		expect(resolveReturnTo(undefined)).toBe('/');
		expect(resolveReturnTo(null)).toBe('/');
		expect(resolveReturnTo({})).toBe('/');
	});

	it('does not return to the login page itself', () => {
		expect(resolveReturnTo({ pathname: '/login', search: '?a=1' })).toBe('/');
	});

	it.each([
		['absolute URL', 'https://evil.example/agents'],
		['javascript URL', 'javascript:alert(1)'],
		['protocol-relative path', '//evil.example/agents'],
		['backslash protocol-relative path', '/\\evil.example/agents'],
		['tab-smuggled protocol-relative path', '/\t/evil.example'],
		['relative path without a leading slash', 'agents'],
	])('rejects an off-app target: %s', (_label, pathname) => {
		expect(resolveReturnTo({ pathname, search: '?approve=cs_1' })).toBe('/');
	});

	it('drops a malformed query or hash rather than splicing it into the path', () => {
		expect(resolveReturnTo({ pathname: '/agents', search: '/evil', hash: 'x' })).toBe(
			'/agents',
		);
	});

	it('ignores non-string fields', () => {
		expect(resolveReturnTo({ pathname: 42 })).toBe('/');
		expect(resolveReturnTo({ pathname: '/agents', search: 1, hash: {} })).toBe('/agents');
	});
});
