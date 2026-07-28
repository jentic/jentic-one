import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import {
	createPlanToolkit,
	createNoAuthCredential,
	suggestToolkitName,
} from '@/shared/lib/provisioningFulfilment';
import { ApiError, ToolkitsService, CredentialsService, CredentialType } from '@/shared/api';

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

describe('suggestToolkitName — agent-first naming', () => {
	it('leads with the requesting agent when its name is known', () => {
		expect(suggestToolkitName('Claude Code', 'posthog-com', 'posthog-api')).toBe(
			'Claude Code toolkit',
		);
	});

	it('falls back to an API-based toolkit name while the agent is unresolved', () => {
		// Even the fallback must READ like a toolkit name — a bare API slug in
		// the field looks like a bug, not a suggestion.
		expect(suggestToolkitName(undefined, 'posthog-com', 'posthog-api')).toBe(
			'posthog-com/posthog-api toolkit',
		);
		expect(suggestToolkitName(undefined, 'httpbin-org')).toBe('httpbin-org toolkit');
	});

	it('treats a blank agent name as unresolved', () => {
		expect(suggestToolkitName('   ', 'posthog-com', 'posthog-api')).toBe(
			'posthog-com/posthog-api toolkit',
		);
	});

	it('clamps a very long agent name so the toolkit name never exceeds 255 chars', () => {
		// Agent names can be up to 255 chars themselves; the suggestion must
		// leave headroom for " toolkit" + a possible "-NN" 409 suffix.
		const suggested = suggestToolkitName('a'.repeat(255), 'v', 'n');
		expect(suggested).toBe(`${'a'.repeat(240)} toolkit`);
		expect(suggested.length + '-20'.length).toBeLessThanOrEqual(255);
	});
});

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

describe('createNoAuthCredential', () => {
	let spy: ReturnType<typeof vi.spyOn>;
	beforeEach(() => {
		spy = vi.spyOn(CredentialsService, 'createCredential');
	});
	afterEach(() => spy.mockRestore());

	it('creates a NO_AUTH credential for the plan API and returns its id', async () => {
		spy.mockResolvedValueOnce({
			credential: { credential_id: 'cred_noauth_1' },
		} as Awaited<ReturnType<typeof CredentialsService.createCredential>>);
		const res = await createNoAuthCredential(
			{ vendor: 'open-meteo-com', name: 'forecast', version: '1.0.0' },
			'open-meteo-com/forecast (no-auth)',
		);
		expect(res.credentialId).toBe('cred_noauth_1');
		expect(spy).toHaveBeenCalledWith({
			requestBody: {
				type: CredentialType.NO_AUTH,
				provider: 'static',
				name: 'open-meteo-com/forecast (no-auth)',
				api: { vendor: 'open-meteo-com', name: 'forecast', version: '1.0.0' },
			},
		});
	});
});
