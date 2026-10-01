import { describe, it, expect } from 'vitest';
import { ApiError } from '@/shared/api';
import { createQueryClient, isClientErrorStatus } from '@/shared/app/query-client';

function apiError(status: number): ApiError {
	return new ApiError(
		{ method: 'GET', url: '/x' },
		{ url: '/x', ok: false, status, statusText: '', body: undefined },
		`HTTP ${status}`,
	);
}

/** Same shape as the module repository errors (WorkspaceApiError & co). */
class WrappedApiError extends Error {
	readonly status: number | null;
	constructor(status: number | null) {
		super('wrapped');
		this.status = status;
	}
}

type RetryFn = (failureCount: number, error: unknown) => boolean;
const retry = createQueryClient().getDefaultOptions().queries?.retry as RetryFn;

describe('isClientErrorStatus', () => {
	it('recognises raw and wrapped 4xx errors', () => {
		expect(isClientErrorStatus(apiError(404))).toBe(true);
		expect(isClientErrorStatus(new WrappedApiError(404))).toBe(true);
		expect(isClientErrorStatus(new WrappedApiError(403))).toBe(true);
	});

	it('rejects 5xx, network (null status) and status-less errors', () => {
		expect(isClientErrorStatus(apiError(500))).toBe(false);
		expect(isClientErrorStatus(new WrappedApiError(502))).toBe(false);
		expect(isClientErrorStatus(new WrappedApiError(null))).toBe(false);
		expect(isClientErrorStatus(new Error('boom'))).toBe(false);
		expect(isClientErrorStatus({ status: 404 })).toBe(false);
		expect(isClientErrorStatus(undefined)).toBe(false);
	});
});

describe('createQueryClient default retry', () => {
	it('never retries a 4xx, raw or wrapped by a module repository', () => {
		expect(retry(0, apiError(404))).toBe(false);
		expect(retry(0, new WrappedApiError(404))).toBe(false);
	});

	it('retries 5xx and network errors twice', () => {
		for (const error of [
			apiError(503),
			new WrappedApiError(500),
			new WrappedApiError(null),
			new Error('x'),
		]) {
			expect(retry(0, error)).toBe(true);
			expect(retry(1, error)).toBe(true);
			expect(retry(2, error)).toBe(false);
		}
	});
});
