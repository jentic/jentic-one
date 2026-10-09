import { describe, it, expect, vi } from 'vitest';
import { checkA11y, render, screen, userEvent } from '@/__tests__/test-utils';
import { RailEventRow } from '@/shared/app/rail/RailEventRow';
import type { EventResponse } from '@/shared/api';
import { adaptEvent, kindForType, primaryDestinationFor } from '@/shared/lib/agentStream';

// The informational rail row an agent's connect request leaves for its owner.
function connectSessionCreated(data: Record<string, unknown>): EventResponse {
	return {
		event_id: 'evt_cs_1',
		type: 'connect_session.created',
		severity: 'info' as EventResponse['severity'],
		summary: "Agent 'scout' asked to connect 'GitHub'",
		requires_action: false,
		created_at: new Date().toISOString(),
		actor_id: 'agnt_scout',
		actor_type: 'agent',
		data,
		_links: { self: '/events/evt_cs_1' },
	};
}

describe('rail — connect_session.created', () => {
	it('buckets connect-session events with credentials', () => {
		expect(kindForType('connect_session.created')).toBe('credential');
	});

	it('lifts the session id and stays informational', () => {
		const ev = adaptEvent(
			connectSessionCreated({
				session_id: 'cs_1',
				agent_id: 'agnt_scout',
				vendor_key: 'github',
				credential_id: 'cred_1',
			}),
		);
		expect(ev.kind).toBe('credential');
		expect(ev.tokens.session_id).toBe('cs_1');
		expect(ev.tokens.agent_id).toBe('agnt_scout');
		expect(ev.requiresAction).toBe(false);
	});

	it('opens the approve deep link, with the requesting agent selected', () => {
		const ev = adaptEvent(
			connectSessionCreated({
				session_id: 'cs 1&x',
				agent_id: 'agnt_scout',
				credential_id: 'cred_1',
			}),
		);
		// The shape of the session's token-less approval_url (router-relative).
		expect(primaryDestinationFor(ev)).toBe('/agents?agent=agnt_scout&approve=cs+1%26x');
		expect(primaryDestinationFor(ev)).not.toContain('poll_token');
	});

	it('falls back to the agent when the event carries no session id', () => {
		const ev = adaptEvent(connectSessionCreated({ agent_id: 'agnt_scout' }));
		expect(primaryDestinationFor(ev)).toBe('/agents?agent=agnt_scout');
	});

	it('navigates to the approval from the row body', async () => {
		const ev = adaptEvent(
			connectSessionCreated({
				session_id: 'cs_1',
				agent_id: 'agnt_scout',
				credential_id: 'cred_1',
			}),
		);
		const onNavigate = vi.fn();
		const { container } = render(
			<RailEventRow ev={ev} onNavigate={onNavigate} actorName="scout" />,
		);
		// Informational: no action-required verb on the row.
		expect(screen.queryByRole('button', { name: 'Review' })).not.toBeInTheDocument();
		await userEvent.click(screen.getByRole('link', { name: /asked to connect/ }));
		expect(onNavigate).toHaveBeenCalledWith('/agents?agent=agnt_scout&approve=cs_1');
		await checkA11y(container);
	});
});
