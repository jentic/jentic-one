import { describe, expect, it } from 'vitest';
import { makeMockCredential } from '@/shared/credentials/mocks/handlers';
import {
	credentialApiGroupKey,
	credentialNamed,
	credentialSiblingHint,
	credentialsSharingApi,
	suggestUniqueName,
} from '@/shared/credentials/lib/credentialIdentity';

const named = (name: string, api = { vendor: 'airlabs.co', name: 'main', version: '' }) =>
	makeMockCredential({ name, api });

describe('credentialsSharingApi', () => {
	it('matches the same vendor and API name in slug form', () => {
		const same = named('a', { vendor: 'AirLabs.co', name: 'Main', version: '2' });
		const other = named('b', { vendor: 'airlabs.co', name: 'flights', version: '' });
		const elsewhere = named('c', { vendor: 'acme.io', name: 'main', version: '' });
		expect(
			credentialsSharingApi([same, other, elsewhere], { vendor: 'airlabs.co', name: 'main' }),
		).toEqual([same]);
	});

	it('counts a vendor-wide credential beside every API of its vendor', () => {
		const vendorWide = named('a', { vendor: 'airlabs.co', name: '', version: '' });
		expect(credentialsSharingApi([vendorWide], { vendor: 'airlabs.co', name: 'main' })).toEqual(
			[vendorWide],
		);
	});

	it('has no siblings before a vendor is known', () => {
		expect(credentialsSharingApi([named('a')], { vendor: ' ', name: '' })).toEqual([]);
	});
});

describe('credentialNamed', () => {
	it('ignores case and surrounding space', () => {
		const cred = named('airlabs.co');
		expect(credentialNamed([cred], '  AirLabs.co ')).toBe(cred);
	});

	it('never matches an empty name', () => {
		expect(credentialNamed([named('')], '  ')).toBeUndefined();
	});
});

describe('suggestUniqueName', () => {
	it('keeps a name no sibling holds', () => {
		expect(suggestUniqueName(' airlabs.co ', [named('other')])).toBe('airlabs.co');
	});

	it('appends the first free number', () => {
		const siblings = [named('airlabs.co'), named('AIRLABS.CO 2')];
		expect(suggestUniqueName('airlabs.co', siblings)).toBe('airlabs.co 3');
	});

	it('counts on from a number the name already ends in', () => {
		expect(suggestUniqueName('airlabs.co 2', [named('airlabs.co 2')])).toBe('airlabs.co 3');
	});
});

describe('credentialApiGroupKey', () => {
	const cred = (api: { vendor: string; name: string; version: string }, catalogId?: string) =>
		makeMockCredential({ api, catalog_api_id: catalogId ?? null });

	it('groups revisions of one API — the version is not part of the key', () => {
		expect(
			credentialApiGroupKey(cred({ vendor: 'slack.com', name: 'web', version: '1' })),
		).toBe(credentialApiGroupKey(cred({ vendor: 'slack.com', name: 'web', version: '2' })));
	});

	it('keeps apart APIs whose names only slug alike', () => {
		expect(
			credentialApiGroupKey(
				cred({ vendor: 'gov.co.uk', name: 'land_registry', version: '' }),
			),
		).not.toBe(
			credentialApiGroupKey(
				cred({ vendor: 'gov.co.uk', name: 'land.registry', version: '' }),
			),
		);
	});

	it('keeps apart catalog entries that share vendor and name', () => {
		const api = { vendor: 'example.co.uk', name: 'default', version: '' };
		expect(credentialApiGroupKey(cred(api, 'example.co.uk/payments'))).not.toBe(
			credentialApiGroupKey(cred(api, 'example.co.uk/accounts')),
		);
	});
});

describe('credentialSiblingHint', () => {
	const cred = (id: string, version: string, hint?: string) =>
		makeMockCredential({
			credential_id: id,
			name: 'Stripe key',
			api: { vendor: 'stripe', name: 'stripe-api', version },
			details: hint ? { hint } : {},
		});

	it('is null when the name is unique', () => {
		const a = cred('cred_aaaaaa', '');
		expect(credentialSiblingHint(a, [a, named('Other')])).toBeNull();
	});

	it('prefers the key hint, then the version, then the id tail', () => {
		const a = cred('cred_aaaaaa', '1', '••••4242');
		const b = cred('cred_bbbbbb', '2', '••••9999');
		expect(credentialSiblingHint(a, [a, b])).toBe('••••4242');
		const c = cred('cred_cccccc', '1');
		const d = cred('cred_dddddd', '2');
		expect(credentialSiblingHint(c, [c, d])).toBe('v1');
		const e = cred('cred_eeeeee', '');
		const f = cred('cred_ffffff', '');
		expect(credentialSiblingHint(e, [e, f])).toBe('…eeeeee');
	});
});
