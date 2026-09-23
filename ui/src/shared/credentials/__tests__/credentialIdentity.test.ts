import { describe, expect, it } from 'vitest';
import { makeMockCredential } from '@/shared/credentials/mocks/handlers';
import {
	credentialNamed,
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
