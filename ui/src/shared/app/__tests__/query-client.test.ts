/**
 * The app QueryClient never retries a 4xx, including one wrapped in a module's
 * sentinel error that carries the HTTP status.
 */
import { describe, it, expect } from 'vitest';
import { ApiError } from '@/shared/api';
import { createQueryClient, isClientErrorLike } from '@/shared/app/query-client';

/** A generated `ApiError` as the client throws it for `status`. */
function apiError(status: number): ApiError {
	return new ApiError(
		{ method: 'GET', url: '/x' },
		{ url: '/x', ok: false, status, statusText: '', body: null },
		'failed',
	);
}

class WrappedError extends Error {
	constructor(readonly status: number | null) {
		super('wrapped');
	}
}

describe('isClientErrorLike', () => {
	it.each([
		[new WrappedError(403), true],
		[new WrappedError(404), true],
		[new WrappedError(408), false],
		[new WrappedError(429), false],
		[apiError(403), true],
		[apiError(408), false],
		[apiError(429), false],
		[apiError(503), false],
		[new WrappedError(500), false],
		[new WrappedError(null), false],
		[new Error('network'), false],
		['not an error', false],
	])('%o → %s', (error, expected) => {
		expect(isClientErrorLike(error)).toBe(expected);
	});
});

describe('createQueryClient retry policy', () => {
	const retry = createQueryClient().getDefaultOptions().queries?.retry as (
		failureCount: number,
		error: unknown,
	) => boolean;

	it('does not retry a wrapped 4xx', () => {
		expect(retry(0, new WrappedError(403))).toBe(false);
		expect(retry(0, new WrappedError(404))).toBe(false);
	});

	it.each([408, 429])('retries a %i, raw or wrapped', (status) => {
		expect(retry(0, apiError(status))).toBe(true);
		expect(retry(1, new WrappedError(status))).toBe(true);
		expect(retry(2, apiError(status))).toBe(false);
	});

	it('does not retry a raw 4xx ApiError', () => {
		expect(retry(0, apiError(403))).toBe(false);
	});

	it('retries a 5xx or network failure twice', () => {
		expect(retry(0, new WrappedError(503))).toBe(true);
		expect(retry(1, new Error('network'))).toBe(true);
		expect(retry(2, new Error('network'))).toBe(false);
	});
});
