import { describe, it, expect } from 'vitest';
import type { EventResponse } from '@/shared/api';
import { adaptEvent, kindForType, primaryDestinationFor } from '@/shared/lib/agentStream';

// The warning an agent's owner gets before a binding reaches its idle limit.
function idleExpiring(data: Record<string, unknown>): EventResponse {
	return {
		event_id: 'evt_idle_1',
		type: 'credential_grant.idle_expiring',
		severity: 'warning' as EventResponse['severity'],
		summary: "Agent 'scout''s access to credential cred_1 ends if unused before 2026-11-25",
		requires_action: true,
		created_at: new Date().toISOString(),
		actor_id: 'agnt_scout',
		actor_type: 'agent',
		data,
		_links: { self: '/events/evt_idle_1' },
	};
}

describe('rail — credential_grant.idle_expiring', () => {
	it('buckets grant events with credentials', () => {
		expect(kindForType('credential_grant.idle_expiring')).toBe('credential');
	});

	it('asks for action and opens the agent it names', () => {
		const ev = adaptEvent(
			idleExpiring({
				agent_id: 'agnt_scout',
				credential_id: 'cred_1',
				idle_expires_at: '2026-11-25T12:00:00Z',
				max_idle_period: 'P14D',
			}),
		);
		expect(ev.kind).toBe('credential');
		expect(ev.requiresAction).toBe(true);
		expect(primaryDestinationFor(ev)).toBe('/agents?agent=agnt_scout');
	});
});
