import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { createPlanToolkit } from '@/shared/lib/provisioningFulfilment';
import { ApiError, ToolkitsService } from '@/shared/api';

function conflict(): ApiError {
	// Minimal ApiError shaped so toRailError reads status 409.
	return new ApiError(
		{ method: 'POST', url: '/toolkits' },
		{ url: '/toolkits', ok: false, status: 409, statusText: 'Conflict', body: {} },
		'Conflict',
	);
}

function ok(id: string, name: string) {
	return { toolkit: { toolkit_id: id, name }, api_key: 'k' } as Awaited<
		ReturnType<typeof ToolkitsService.createToolkit>
	>;
}

describe('createPlanToolkit — 409 name disambiguation', () => {
	let spy: ReturnType<typeof vi.spyOn>;
	beforeEach(() => {
		spy = vi.spyOn(ToolkitsService, 'createToolkit');
	});
	afterEach(() => spy.mockRestore());

	it('returns immediately when the name is free', async () => {
		spy.mockResolvedValueOnce(ok('tk_1', 'posthog-com/posthog-api'));
		const res = await createPlanToolkit('posthog-com/posthog-api');
		expect(res.toolkitId).toBe('tk_1');
		expect(spy).toHaveBeenCalledTimes(1);
		expect(spy).toHaveBeenCalledWith({ requestBody: { name: 'posthog-com/posthog-api' } });
	});

	it('retries with a numeric suffix on 409 until one succeeds', async () => {
		spy.mockRejectedValueOnce(conflict())
			.mockRejectedValueOnce(conflict())
			.mockResolvedValueOnce(ok('tk_3', 'posthog-com/posthog-api-3'));
		const res = await createPlanToolkit('posthog-com/posthog-api');
		expect(res.toolkitId).toBe('tk_3');
		expect(spy).toHaveBeenNthCalledWith(1, {
			requestBody: { name: 'posthog-com/posthog-api' },
		});
		expect(spy).toHaveBeenNthCalledWith(2, {
			requestBody: { name: 'posthog-com/posthog-api-2' },
		});
		expect(spy).toHaveBeenNthCalledWith(3, {
			requestBody: { name: 'posthog-com/posthog-api-3' },
		});
	});

	it('does NOT retry a non-409 error', async () => {
		const boom = new ApiError(
			{ method: 'POST', url: '/toolkits' },
			{ url: '/toolkits', ok: false, status: 500, statusText: 'Server Error', body: {} },
			'boom',
		);
		spy.mockRejectedValueOnce(boom);
		await expect(createPlanToolkit('x')).rejects.toMatchObject({ status: 500 });
		expect(spy).toHaveBeenCalledTimes(1);
	});
});
