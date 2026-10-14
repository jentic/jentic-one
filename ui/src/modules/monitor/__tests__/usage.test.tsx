/**
 * Agent-dimension usage labels. The rollup keys a row by `actor_type/actor_id`
 * and carries no name, so the agents lens names each row from the actor
 * directory (`withActorNames`), as the rest of the app does.
 *
 * Historical service-account rows: executions recorded before the
 * service-account migration keep `actor_type = 'service_account'`, so
 * `GET /monitoring/usage?group_by=agent` can still return `service_account/sva_…`
 * keys. Monitor must label them as retired, not show a bare `sva_…` id that
 * reads like a live agent.
 */
import { describe, expect, it } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/__tests__/test-utils';
import type { UsageResponse } from '@/modules/monitor/api';
import { smartInitials, shortActorId } from '@/shared/lib';
import {
	HIDDEN_AGENT_LABEL,
	isUnnamedActor,
	usageToEntityRows,
	withActorNames,
	type ActorNameSource,
} from '@/modules/monitor/lib/usage';
import { UsageBreakdown } from '@/modules/monitor/components/UsageBreakdown';
import type { UsageLens } from '@/modules/monitor/lib/palette';

function agentUsage(keys: string[]): UsageResponse {
	return {
		since: 0,
		until: 3600,
		bucket_seconds: 3600,
		group_by: 'agent',
		stats: {
			total: keys.length,
			success: keys.length,
			failed: 0,
			pending: 0,
			avg_ms: 100,
			p50_ms: 90,
			p95_ms: 200,
			active_now: 0,
		},
		buckets: [],
		top: keys.map((key, i) => ({
			key,
			label: key,
			total: keys.length - i,
			success: keys.length - i,
			failed: 0,
			avg_ms: 100,
			trend: [],
		})),
	};
}

describe('usageToEntityRows (group_by=agent)', () => {
	it('labels an agent row by its bare id', () => {
		const [row] = usageToEntityRows(agentUsage(['agent/agnt_1']));
		expect(row.label).toBe('agnt_1');
	});

	it('labels a historical service_account row as retired', () => {
		const [row] = usageToEntityRows(agentUsage(['service_account/sva_x']));
		expect(row.id).toBe('service_account/sva_x');
		expect(row.label).toBe('sva_x (retired service account)');
	});

	it('keeps an empty actor id as Unattributed', () => {
		const [row] = usageToEntityRows(agentUsage(['service_account/']));
		expect(row.label).toBe('Unattributed');
	});
});

describe('withActorNames', () => {
	const directory = (
		names: Record<string, string>,
		{ hidden = [] as string[], isLoading = false, initials = new Map<string, string>() } = {},
	): ActorNameSource => ({
		resolve: (id) => names[id],
		isHidden: (id) => hidden.includes(id),
		isLoading,
		initials,
	});
	const AGENT_A = 'agnt_6accc351d881e4d9aaaa';
	const AGENT_B = 'agnt_6accc351d881e4d9bbbb';

	it('names two agents whose ids read alike, with distinct fleet initials', () => {
		const rows = usageToEntityRows(agentUsage([`agent/${AGENT_A}`, `agent/${AGENT_B}`]));
		const initials = smartInitials([
			{ id: AGENT_A, name: 'support-triage', createdAt: '2026-01-01T00:00:00Z' },
			{ id: AGENT_B, name: 'support-triage-eu', createdAt: '2026-01-02T00:00:00Z' },
		]);
		const named = withActorNames(
			rows,
			directory(
				{ [AGENT_A]: 'support-triage', [AGENT_B]: 'support-triage-eu' },
				{ initials },
			),
		);
		expect(named.map((r) => r.label)).toEqual(['support-triage', 'support-triage-eu']);
		expect(named.map((r) => r.initials)).toEqual(['ST', 'STE']);
		expect(named.map((r) => r.actor?.resolution)).toEqual(['resolved', 'resolved']);
		// The row keeps its id, so the drill-down and the colours are unchanged.
		expect(named[0].id).toBe(`agent/${AGENT_A}`);
	});

	it('shows an unknown agent as its short id, never the whole id', () => {
		const [row] = withActorNames(
			usageToEntityRows(agentUsage([`agent/${AGENT_A}`])),
			directory({}),
		);
		expect(row.label).toBe('agnt_…aaaa');
		expect(row.actor).toEqual({ id: AGENT_A, type: 'agent', resolution: 'unknown' });
		expect(row.initials).toBe('?');
		expect(isUnnamedActor(row)).toBe(true);
	});

	it('never shows the whole id as the name while the directory loads', () => {
		const [row] = withActorNames(
			usageToEntityRows(agentUsage([`agent/${AGENT_A}`])),
			directory({}, { isLoading: true }),
		);
		expect(row.label).not.toBe(AGENT_A);
		expect(row.actor?.resolution).toBe('loading');
		expect(isUnnamedActor(row)).toBe(true);
	});

	it('says an agent the caller may not see is not visible', () => {
		const [row] = withActorNames(
			usageToEntityRows(agentUsage([`agent/${AGENT_A}`])),
			directory({}, { hidden: [AGENT_A] }),
		);
		expect(row.label).toBe(HIDDEN_AGENT_LABEL);
		expect(row.actor?.resolution).toBe('hidden');
		// A sentence, not an id: it keeps the text face.
		expect(isUnnamedActor(row)).toBe(false);
	});

	it("names a user row by the user's display name", () => {
		const [row] = withActorNames(
			usageToEntityRows(agentUsage(['user/usr_admin_1'])),
			directory({ usr_admin_1: 'Ada Admin' }),
		);
		expect(row.label).toBe('Ada Admin');
		// Users are not in the agent fleet: their avatar reads the name's letters.
		expect(row.initials).toBeUndefined();
	});

	it('leaves retired service-account and Unattributed rows as they are', () => {
		const rows = usageToEntityRows(agentUsage(['service_account/sva_x', 'agent/']));
		const named = withActorNames(rows, directory({ sva_x: 'never used' }));
		expect(named).toEqual(rows);
	});
});

describe('shortActorId', () => {
	it('keeps the type prefix and the last four characters', () => {
		expect(shortActorId('agnt_6accc351d881e4d9')).toBe('agnt_…e4d9');
		expect(shortActorId('usr_0123456789abcdef')).toBe('usr_…cdef');
	});

	it('leaves an id short enough to read whole', () => {
		expect(shortActorId('agnt_1')).toBe('agnt_1');
		expect(shortActorId('agent_billing')).toBe('agent_billing');
	});
});

describe('UsageBreakdown', () => {
	const noop = (_lens: UsageLens) => {};

	it('labels a named agent row, its avatar and its link by the name', async () => {
		const rows = withActorNames(usageToEntityRows(agentUsage(['agent/agnt_6accc351d881'])), {
			resolve: (id) => (id === 'agnt_6accc351d881' ? 'support-triage' : undefined),
			isHidden: () => false,
			isLoading: false,
			initials: new Map([['agnt_6accc351d881', 'ST']]),
		});
		renderWithProviders(
			<UsageBreakdown
				lens="agents"
				onLensChange={noop}
				rows={rows}
				rowHref={() => '/app/monitor?show=calls'}
			/>,
		);
		const row = await screen.findByRole('row', { name: 'View executions for support-triage' });
		expect(row.querySelector('[data-initials]')).toHaveAttribute('data-initials', 'ST');
		expect(screen.queryByText(/agnt_/)).not.toBeInTheDocument();
	});

	it('names an unknown agent row by its short id, with the whole id on its link', async () => {
		const rows = withActorNames(
			usageToEntityRows(agentUsage(['agent/agnt_6accc351d881e4d9'])),
			{
				resolve: () => undefined,
				isHidden: () => false,
				isLoading: false,
				initials: new Map(),
			},
		);
		renderWithProviders(
			<UsageBreakdown
				lens="agents"
				onLensChange={noop}
				rows={rows}
				rowHref={() => '/app/monitor?show=calls'}
			/>,
		);
		expect(await screen.findByText('agnt_…e4d9')).toBeInTheDocument();
		expect(
			screen.getByRole('row', {
				name: 'View executions for agnt_…e4d9 (agnt_6accc351d881e4d9)',
			}),
		).toBeInTheDocument();
	});

	it('renders a historical service-account row unlinked, labelled as retired', async () => {
		const agents = usageToEntityRows(agentUsage(['agent/agnt_1', 'service_account/sva_x']));
		renderWithProviders(
			<UsageBreakdown
				lens="agents"
				onLensChange={noop}
				rows={agents}
				rowHref={(row) =>
					row.id.startsWith('agent/') ? `/app/monitor?actor_id=${row.label}` : null
				}
			/>,
		);
		const label = await screen.findByText('sva_x (retired service account)');
		expect(label.closest('a')).toBeNull();
		expect(screen.getByText('agnt_1').closest('a')).toHaveAttribute(
			'href',
			expect.stringContaining('actor_id=agnt_1'),
		);
	});

	it('reports lens changes to the owner', async () => {
		const seen: UsageLens[] = [];
		renderWithProviders(
			<UsageBreakdown lens="apis" onLensChange={(l) => seen.push(l)} rows={[]} />,
		);
		await userEvent.click(screen.getByRole('button', { name: 'Agents' }));
		expect(seen).toEqual(['agents']);
	});

	it('sorts by success rate worst-first, then flips on a second click', async () => {
		const usage = agentUsage(['agent/a', 'agent/b', 'agent/c']);
		// a: 3 calls 100%, b: 2 calls 50%, c: 1 call 0%
		usage.top[1].success = 1;
		usage.top[2].success = 0;
		const rows = usageToEntityRows(usage);
		renderWithProviders(<UsageBreakdown lens="agents" onLensChange={noop} rows={rows} />);
		const names = () =>
			// The header row is hidden on narrow viewports, so match data rows only.
			screen
				.getAllByRole('row', { hidden: true })
				.map((r) => within(r).queryByText(/^[abc]$/)?.textContent)
				.filter(Boolean);
		expect(names()).toEqual(['a', 'b', 'c']);
		// Sort headers live in the desktop header row (hidden on the narrow test
		// viewport), so click them without the visibility check.
		const sortBySuccess = () =>
			screen.getByRole('button', { name: 'Sort by success', hidden: true });
		fireEvent.click(sortBySuccess());
		expect(names()).toEqual(['c', 'b', 'a']);
		fireEvent.click(sortBySuccess());
		expect(names()).toEqual(['a', 'b', 'c']);
	});
});
