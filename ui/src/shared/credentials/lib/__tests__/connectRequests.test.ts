import { describe, expect, it } from 'vitest';
import type { ConnectSessionSummaryResponse } from '@/shared/api';
import {
	groupConnectRequestsByAgent,
	summariseConnectTargets,
} from '@/shared/credentials/lib/connectRequests';

function row(
	session_id: string,
	agent_id: string | null,
	created_at: string,
	vendor_display_name = 'GitHub',
): ConnectSessionSummaryResponse {
	return {
		session_id,
		agent_id,
		created_at,
		vendor_display_name,
		vendor_key: vendor_display_name.toLowerCase(),
		requested_by_actor_id: agent_id ?? 'usr_1',
		state: 'created' as ConnectSessionSummaryResponse['state'],
		credential_id: `cred_${session_id}`,
	};
}

describe('groupConnectRequestsByAgent', () => {
	it('collapses per agent, oldest request first, agents ordered by their oldest ask', () => {
		const groups = groupConnectRequestsByAgent([
			row('s3', 'agnt_b', '2026-10-09T10:05:00Z'),
			row('s1', 'agnt_a', '2026-10-09T10:10:00Z'),
			row('s2', 'agnt_b', '2026-10-09T10:01:00Z'),
			row('s4', null, '2026-10-09T09:00:00Z'),
		]);
		expect(groups.map((g) => g.agentId)).toEqual(['agnt_b', 'agnt_a']);
		expect(groups[0].sessions.map((s) => s.session_id)).toEqual(['s2', 's3']);
		expect(groups[0].since).toBe('2026-10-09T10:01:00Z');
	});

	it('returns nothing for no sessions', () => {
		expect(groupConnectRequestsByAgent([])).toEqual([]);
	});
});

describe('summariseConnectTargets', () => {
	const at = '2026-10-09T10:00:00Z';
	it('names one, two, or two and a count of distinct targets', () => {
		expect(summariseConnectTargets([row('a', 'x', at, 'GitHub')])).toBe('GitHub');
		expect(
			summariseConnectTargets([row('a', 'x', at, 'GitHub'), row('b', 'x', at, 'GitHub')]),
		).toBe('GitHub');
		expect(
			summariseConnectTargets([row('a', 'x', at, 'GitHub'), row('b', 'x', at, 'Slack')]),
		).toBe('GitHub and Slack');
		expect(
			summariseConnectTargets([
				row('a', 'x', at, 'GitHub'),
				row('b', 'x', at, 'Slack'),
				row('c', 'x', at, 'Linear'),
				row('d', 'x', at, 'Jira'),
			]),
		).toBe('GitHub, Slack and 2 more');
	});
});
