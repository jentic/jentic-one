import { describe, expect, it } from 'vitest';
import { KeyRound, LogIn, Ticket, UserRound } from 'lucide-react';
import { CredentialType } from '@/shared/credentials/api';
import {
	CREDENTIAL_TYPE_ICONS,
	FALLBACK_AUTH_TYPE_ICON,
	credentialTypeIcon,
	schemeTypeIcon,
} from '@/shared/credentials/lib/authTypeIcons';

describe('CREDENTIAL_TYPE_ICONS', () => {
	it('gives every supported credential type its own icon', () => {
		const types = Object.values(CredentialType);
		for (const type of types) expect(CREDENTIAL_TYPE_ICONS[type]).toBeDefined();
		expect(Object.keys(CREDENTIAL_TYPE_ICONS).sort()).toEqual([...types].sort());
		expect(new Set(types.map((t) => CREDENTIAL_TYPE_ICONS[t])).size).toBe(types.length);
	});
});

describe('credentialTypeIcon', () => {
	it('reads the shared map, and the generic key for an unknown type', () => {
		expect(credentialTypeIcon(CredentialType.BEARER_TOKEN)).toBe(Ticket);
		expect(credentialTypeIcon('future_type')).toBe(FALLBACK_AUTH_TYPE_ICON);
		expect(credentialTypeIcon('constructor')).toBe(FALLBACK_AUTH_TYPE_ICON);
	});
});

describe('schemeTypeIcon', () => {
	it('maps every spelling of a declared scheme to its credential type icon', () => {
		expect(schemeTypeIcon('bearer')).toBe(Ticket);
		expect(schemeTypeIcon('apiKey')).toBe(KeyRound);
		expect(schemeTypeIcon('api_key')).toBe(KeyRound);
		expect(schemeTypeIcon('Basic')).toBe(UserRound);
		expect(schemeTypeIcon('oauth2')).toBe(LogIn);
	});

	it('falls back to the generic key for a scheme with no credential type', () => {
		expect(schemeTypeIcon('openIdConnect')).toBe(FALLBACK_AUTH_TYPE_ICON);
		expect(schemeTypeIcon('')).toBe(FALLBACK_AUTH_TYPE_ICON);
	});
});
