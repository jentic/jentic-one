/**
 * The app QueryClient never retries a 4xx, including one wrapped in a module's
 * sentinel error that carries the HTTP status.
 */
import { describe, it, expect } from 'vitest';
import { createQueryClient, isClientErrorLike } from '@/shared/app/query-client';

class WrappedError extends Error {
	constructor(readonly status: number | null) {
		super('wrapped');
	}
}

describe('isClientErrorLike', () => {
	it.each([
		[new WrappedError(403), true],
		[new WrappedError(404), true],
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

	it('retries a 5xx or network failure twice', () => {
		expect(retry(0, new WrappedError(503))).toBe(true);
		expect(retry(1, new Error('network'))).toBe(true);
		expect(retry(2, new Error('network'))).toBe(false);
	});
});
