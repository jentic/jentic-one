/**
 * Agent-dimension usage labels, focused on historical service-account rows
 * (theme 8, OQ-3). Service accounts were migrated to agents, but executions
 * recorded before the migration keep `actor_type = 'service_account'`, so
 * `GET /monitoring/usage?group_by=agent` can still return `service_account/sva_…`
 * keys. Monitor must label them as retired, not show a bare `sva_…` id that
 * reads like a live agent.
 */
import { describe, expect, it } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/__tests__/test-utils';
import type { UsageResponse } from '@/modules/monitor/api';
import { usageToEntityRows } from '@/modules/monitor/lib/usage';
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

describe('UsageBreakdown', () => {
	const noop = (_lens: UsageLens) => {};

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
