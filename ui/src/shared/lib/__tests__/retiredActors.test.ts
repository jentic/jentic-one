import { describe, expect, it } from 'vitest';
import {
	SERVICE_ACCOUNT_SUCCESSOR_REGISTRAR,
	holdsMigratedServiceAccountKey,
	retiredServiceAccountLabel,
} from '@/shared/lib';

const migrated = {
	registeredBy: SERVICE_ACCOUNT_SUCCESSOR_REGISTRAR,
	keyStatus: 'active',
	keyRotatedAt: null,
	keyCreatedBy: SERVICE_ACCOUNT_SUCCESSOR_REGISTRAR,
};

describe('holdsMigratedServiceAccountKey', () => {
	it('is true for a successor still on its migration-created, never-rotated key', () => {
		expect(holdsMigratedServiceAccountKey(migrated)).toBe(true);
	});

	it('is false once the key was rotated or revoked', () => {
		expect(
			holdsMigratedServiceAccountKey({ ...migrated, keyRotatedAt: '2026-02-01T00:00:00Z' }),
		).toBe(false);
		expect(holdsMigratedServiceAccountKey({ ...migrated, keyStatus: 'revoked' })).toBe(false);
	});

	it('is false for an ordinary agent or a key someone else created', () => {
		expect(holdsMigratedServiceAccountKey({ ...migrated, registeredBy: 'usr_1' })).toBe(false);
		expect(holdsMigratedServiceAccountKey({ ...migrated, keyCreatedBy: 'usr_1' })).toBe(false);
	});

	it('is false while the key info is unknown', () => {
		expect(
			holdsMigratedServiceAccountKey({
				...migrated,
				keyStatus: undefined,
				keyCreatedBy: undefined,
			}),
		).toBe(false);
	});
});

describe('retiredServiceAccountLabel', () => {
	it('marks the raw id as retired', () => {
		expect(retiredServiceAccountLabel('sva_1')).toBe('sva_1 (retired service account)');
	});
});
