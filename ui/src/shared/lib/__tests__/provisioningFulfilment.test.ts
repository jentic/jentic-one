import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { createNoAuthCredential } from '@/shared/lib/provisioningFulfilment';
import { ApiError, CredentialsService, CredentialType } from '@/shared/api';

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

	it('omits blank name/version from the API reference', async () => {
		spy.mockResolvedValueOnce({
			credential: { credential_id: 'cred_noauth_2' },
		} as Awaited<ReturnType<typeof CredentialsService.createCredential>>);
		await createNoAuthCredential({ vendor: 'country-is' }, 'country-is (no-auth)');
		expect(spy).toHaveBeenCalledWith({
			requestBody: {
				type: CredentialType.NO_AUTH,
				provider: 'static',
				name: 'country-is (no-auth)',
				api: { vendor: 'country-is', name: undefined, version: undefined },
			},
		});
	});

	it('wraps a server failure in a rail-friendly error', async () => {
		const boom = new ApiError(
			{ method: 'POST', url: '/credentials' },
			{
				url: '/credentials',
				ok: false,
				status: 500,
				statusText: 'Server Error',
				body: {},
			},
			'boom',
		);
		spy.mockRejectedValueOnce(boom);
		await expect(createNoAuthCredential({ vendor: 'v' }, 'n')).rejects.toMatchObject({
			status: 500,
		});
	});
});
