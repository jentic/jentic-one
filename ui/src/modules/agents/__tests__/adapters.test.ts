import { describe, it, expect } from 'vitest';
import {
	agentToEntity,
	isServiceAccountSuccessor,
	toActorStatus,
	ACTIONS_FOR_STATUS,
} from '@/modules/agents/api/types';
import type { AgentResponse } from '@/shared/api';

describe('agents adapters', () => {
	it('maps an AgentResponse into a UI entity, collapsing attribution', () => {
		const res: AgentResponse = {
			id: 'agnt_1',
			name: 'bot',
			description: null,
			owner_id: null,
			registered_by: 'self',
			parent_agent_id: null,
			approved_by: 'usr_admin',
			status: 'active',
			denial_reason: null,
			denied_by: null,
			created_at: '2026-01-01T00:00:00Z',
			approved_at: '2026-01-02T00:00:00Z',
		};
		const e = agentToEntity(res);
		expect(e.status).toBe('active');
		expect(e.attribution).toEqual({
			registeredBy: 'self',
			approvedBy: 'usr_admin',
			deniedBy: null,
		});
	});

	it('recognises a theme-8 service-account successor by its registrar stamp', () => {
		const base: AgentResponse = {
			id: 'agnt_1',
			name: 'service-account:sva_1',
			description: null,
			owner_id: 'usr_admin',
			registered_by: 'system:theme8-sa-migration',
			parent_agent_id: null,
			approved_by: 'usr_admin',
			status: 'active',
			denial_reason: null,
			denied_by: null,
			created_at: '2026-01-01T00:00:00Z',
			approved_at: '2026-01-02T00:00:00Z',
		};
		expect(isServiceAccountSuccessor(agentToEntity(base))).toBe(true);
		// The name alone is not the signal — only the immutable registrar stamp.
		expect(
			isServiceAccountSuccessor(agentToEntity({ ...base, registered_by: 'usr_admin' })),
		).toBe(false);
	});

	it('defaults an unknown status to the terminal archived state (defensive)', () => {
		// Unknown statuses map to a terminal state that exposes no lifecycle
		// actions, so an unrecognized value can never surface approve/deny.
		expect(toActorStatus('weird')).toBe('archived');
		expect(toActorStatus('archived')).toBe('archived');
		expect(toActorStatus('pending')).toBe('pending');
	});

	it('exposes the verified state-machine actions per status', () => {
		// archive is allowed from any non-archived status (the backend only
		// rejects archiving an already-archived actor).
		expect(ACTIONS_FOR_STATUS.pending).toEqual(['approve', 'deny', 'archive']);
		expect(ACTIONS_FOR_STATUS.active).toEqual(['disable', 'archive']);
		expect(ACTIONS_FOR_STATUS.disabled).toEqual(['enable', 'archive']);
		expect(ACTIONS_FOR_STATUS.rejected).toEqual(['archive']);
		expect(ACTIONS_FOR_STATUS.archived).toEqual([]);
	});
});
