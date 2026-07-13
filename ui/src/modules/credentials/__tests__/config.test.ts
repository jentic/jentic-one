import { describe, expect, it } from 'vitest';
import type { ProviderDiscoveryEntryResponse } from '@/shared/api';
import { CredentialType } from '@/modules/credentials/api';
import {
	isManagedProvider,
	managedProviderUnavailableMessage,
	providerOptions,
} from '@/modules/credentials/config';

/** Build a minimal discovery entry for the given id + configured state. */
function entry(id: string, configured: boolean, managed = true): ProviderDiscoveryEntryResponse {
	return { id, configured, managed, label: id, types: [CredentialType.OAUTH2] };
}

const PIPEDREAM_READY = [entry('pipedream', true)];
const PIPEDREAM_UNCONFIGURED = [entry('pipedream', false)];

describe('credentials/config', () => {
	it('offers only static for non-oauth2 types', () => {
		expect(providerOptions(CredentialType.API_KEY, PIPEDREAM_READY).map((o) => o.id)).toEqual([
			'static',
		]);
		expect(
			providerOptions(CredentialType.BEARER_TOKEN, PIPEDREAM_READY).map((o) => o.id),
		).toEqual(['static']);
	});

	it('includes direct_oauth2 as first option for oauth2 type', () => {
		const options = providerOptions(CredentialType.OAUTH2);
		expect(options[0].id).toBe('direct_oauth2');
		expect(options.map((o) => o.id)).toEqual(['direct_oauth2', 'static']);
	});

	it('omits pipedream when no providers are discovered', () => {
		expect(providerOptions(CredentialType.OAUTH2, []).map((o) => o.id)).toEqual([
			'direct_oauth2',
			'static',
		]);
	});

	it('omits pipedream when discovered but not configured', () => {
		expect(
			providerOptions(CredentialType.OAUTH2, PIPEDREAM_UNCONFIGURED).map((o) => o.id),
		).toEqual(['direct_oauth2', 'static']);
	});

	it('adds the managed (pipedream) option when discovery reports it configured', () => {
		expect(providerOptions(CredentialType.OAUTH2, PIPEDREAM_READY).map((o) => o.id)).toEqual([
			'direct_oauth2',
			'static',
			'pipedream',
		]);
	});

	it('direct_oauth2 is not managed', () => {
		const directOpt = providerOptions(CredentialType.OAUTH2).find(
			(o) => o.id === 'direct_oauth2',
		);
		expect(directOpt?.managed).toBe(false);
	});

	it('recognises managed providers', () => {
		expect(isManagedProvider('pipedream')).toBe(true);
		expect(isManagedProvider('static')).toBe(false);
		expect(isManagedProvider('direct_oauth2')).toBe(false);
		expect(isManagedProvider(null)).toBe(false);
	});

	it('maps a managed-provider create failure to a friendly message', () => {
		expect(managedProviderUnavailableMessage('pipedream', { status: 500 })).toMatch(
			/Pipedream isn't enabled/i,
		);
		expect(managedProviderUnavailableMessage('pipedream', { status: 400 })).toMatch(
			/Pipedream isn't enabled/i,
		);
		// Non-managed providers never get the Pipedream message.
		expect(managedProviderUnavailableMessage('static', { status: 500 })).toBeNull();
		expect(managedProviderUnavailableMessage('direct_oauth2', { status: 500 })).toBeNull();
		// A managed provider that fails for an unrelated reason (e.g. 404) is not masked.
		expect(managedProviderUnavailableMessage('pipedream', { status: 404 })).toBeNull();
	});
});
