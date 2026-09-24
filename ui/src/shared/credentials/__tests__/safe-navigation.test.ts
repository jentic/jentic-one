import { afterEach, describe, expect, it, vi } from 'vitest';
import {
	UnsafeVendorUrlError,
	assertHttpsVendorUrl,
	assignVendorUrl,
	isHttpsVendorUrl,
	openVendorUrl,
} from '@/shared/credentials/lib/safe-navigation';

describe('safe-navigation', () => {
	afterEach(() => {
		vi.restoreAllMocks();
	});

	describe('isHttpsVendorUrl', () => {
		it('accepts an https URL', () => {
			expect(isHttpsVendorUrl('https://github.com/login/oauth/authorize?x=1')).toBe(true);
		});

		it.each([
			['plain http', 'http://vendor.example/login'],
			['javascript scheme', 'javascript:alert(1)'],
			['data URI', 'data:text/html,<script>alert(1)</script>'],
			['file scheme', 'file:///etc/passwd'],
			['unparsable', 'not a url'],
			['empty', ''],
			['null', null],
			['undefined', undefined],
		])('rejects %s', (_label, url) => {
			expect(isHttpsVendorUrl(url)).toBe(false);
		});
	});

	describe('assertHttpsVendorUrl', () => {
		it('returns the parsed URL on https', () => {
			const parsed = assertHttpsVendorUrl('https://accounts.google.com/o/oauth2/v2/auth');
			expect(parsed.protocol).toBe('https:');
			expect(parsed.hostname).toBe('accounts.google.com');
		});

		it('throws UnsafeVendorUrlError on javascript: URL', () => {
			expect(() => assertHttpsVendorUrl('javascript:alert(1)')).toThrow(UnsafeVendorUrlError);
		});

		it('throws UnsafeVendorUrlError on empty', () => {
			expect(() => assertHttpsVendorUrl('')).toThrow(UnsafeVendorUrlError);
		});
	});

	describe('openVendorUrl', () => {
		it('delegates to window.open for https', () => {
			const spy = vi.spyOn(window, 'open').mockReturnValue(null);
			openVendorUrl('https://example.com/authorize', '_blank', 'noopener,noreferrer');
			expect(spy).toHaveBeenCalledWith(
				'https://example.com/authorize',
				'_blank',
				'noopener,noreferrer',
			);
		});

		it('throws BEFORE calling window.open on unsafe URL', () => {
			const spy = vi.spyOn(window, 'open').mockReturnValue(null);
			expect(() => openVendorUrl('javascript:alert(1)')).toThrow(UnsafeVendorUrlError);
			expect(spy).not.toHaveBeenCalled();
		});
	});

	describe('assignVendorUrl', () => {
		it('throws BEFORE navigating on unsafe URL', () => {
			// ``window.location.assign`` isn't spy-able in vitest's browser
			// environment (it delegates to a live navigation) — since the
			// guard is meant to fire BEFORE that call ever happens, asserting
			// the throw is sufficient. If the throw is skipped, the browser
			// really would try to navigate.
			expect(() => assignVendorUrl('data:text/html,x')).toThrow(UnsafeVendorUrlError);
		});
	});
});
