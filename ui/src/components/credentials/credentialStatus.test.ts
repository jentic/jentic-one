import { describe, it, expect } from 'vitest';
import { deriveCredentialStatus } from './credentialStatus';

/**
 * Pure-function contract for the credential health badge. This is the single
 * source of truth every StatusDot reads from, so the four tones and their
 * precedence are pinned here rather than re-asserted in each consuming view.
 */
describe('deriveCredentialStatus', () => {
	it('is broken when healthy === false (manual copy)', () => {
		const s = deriveCredentialStatus({
			auth_type: 'bearer',
			healthy: false,
			last_used_at: 1700000000,
			health_checked_at: 1700000000,
		});
		expect(s.tone).toBe('broken');
		expect(s.label).toBe('Credential rejected');
		expect(s.detail).toMatch(/expired or revoked/i);
	});

	it('is broken with reconnect copy for Pipedream', () => {
		const s = deriveCredentialStatus({
			auth_type: 'pipedream_oauth',
			healthy: false,
			last_used_at: null,
			health_checked_at: null,
		});
		expect(s.tone).toBe('broken');
		expect(s.label).toBe('Connection rejected');
		expect(s.detail).toMatch(/reconnect/i);
	});

	it('is ok ("Working") when healthy === true, even with no last_used_at', () => {
		const s = deriveCredentialStatus({
			auth_type: 'bearer',
			healthy: true,
			last_used_at: null,
			health_checked_at: 1700000000,
		});
		expect(s.tone).toBe('ok');
		expect(s.label).toBe('Working');
	});

	it('is ok ("In use") on a legacy last_used_at with null healthy', () => {
		const s = deriveCredentialStatus({
			auth_type: 'apiKey',
			healthy: null,
			last_used_at: 1700000000,
			health_checked_at: null,
		});
		expect(s.tone).toBe('ok');
		expect(s.label).toBe('In use');
	});

	it('is neutral ("Never used") when there is no signal at all', () => {
		const s = deriveCredentialStatus({
			auth_type: 'bearer',
			healthy: null,
			last_used_at: null,
			health_checked_at: null,
		});
		expect(s.tone).toBe('neutral');
		expect(s.label).toBe('Never used');
		// Critically NOT 'unknown' — a fresh credential is calm, not a warning.
		expect(s.tone).not.toBe('unknown');
	});

	it('prefers healthy=false over a recent last_used_at', () => {
		const s = deriveCredentialStatus({
			auth_type: 'bearer',
			healthy: false,
			last_used_at: 1700000000,
			health_checked_at: 1700000100,
		});
		expect(s.tone).toBe('broken');
	});
});
