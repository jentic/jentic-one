import { describe, expect, it } from 'vitest';
import { renderWithProviders, screen, within } from '@/__tests__/test-utils';
import { AgentStatStrip } from '@/modules/agents/components/flat/AgentStatStrip';
import type { ApiTileStats } from '@/modules/agents/lib/apiTiles';
import type { ActorUsageDetail } from '@/modules/agents/api';

const BASE: ApiTileStats = {
	configured: 1,
	needsSetup: 0,
	operations: 4,
	operationsAtLeast: false,
	operationsChecking: false,
	blockedBindings: 0,
	fullyBlockedApis: 0,
};

function renderStrip(
	access: ApiTileStats,
	extra: {
		usage?: ActorUsageDetail | null;
		lastActivity?: { at: string | null } | null;
	} = {},
) {
	renderWithProviders(
		<AgentStatStrip
			agentName="agent-one"
			apiCount={1}
			access={access}
			credentialCount={1}
			usage={extra.usage ?? null}
			lastActivity={extra.lastActivity ?? null}
		/>,
	);
	return within(screen.getByTestId('agent-stat-strip'));
}

describe('AgentStatStrip — the KPI row', () => {
	it('states the reachable figure once every row’s rules are read', () => {
		const strip = renderStrip(BASE);
		expect(strip.getByTestId('stat-operations-value')).toHaveTextContent(/^4$/);
		expect(strip.getByTestId('stat-apis-value')).toHaveTextContent(/^1$/);
		expect(strip.getByTestId('stat-credentials-value')).toHaveTextContent(/^1$/);
	});

	it('claims nothing when a row can’t read its rules (Status unavailable)', () => {
		const strip = renderStrip({ ...BASE, operations: null });
		expect(strip.queryByTestId('stat-operations')).not.toBeInTheDocument();
	});

	it('holds the figure on a skeleton while a row is still checking access', () => {
		const strip = renderStrip({ ...BASE, operationsChecking: true });
		expect(strip.getByTestId('stat-operations')).toBeInTheDocument();
		expect(strip.queryByTestId('stat-operations-value')).not.toBeInTheDocument();
	});

	it('marks a floor with a plus', () => {
		const strip = renderStrip({ ...BASE, operationsAtLeast: true });
		expect(strip.getByTestId('stat-operations-value')).toHaveTextContent(/^4\+$/);
	});

	it('names blocked credentials under Credentials, not APIs, without a hue', () => {
		const strip = renderStrip({ ...BASE, blockedBindings: 5 });
		const note = strip.getByTestId('stat-credentials-blocked');
		expect(note).toHaveTextContent('5 blocked');
		expect(strip.getByTestId('stat-credentials')).toContainElement(note);
		expect(note.closest('dd')?.className).not.toMatch(/warning|caution|success/);
		// No API is fully blocked, so the APIs cell says nothing about it.
		expect(strip.queryByTestId('stat-apis-blocked')).not.toBeInTheDocument();
	});

	it('names a fully blocked API under APIs', () => {
		const strip = renderStrip({ ...BASE, blockedBindings: 2, fullyBlockedApis: 1 });
		expect(strip.getByTestId('stat-apis-blocked')).toHaveTextContent('1 fully blocked');
		expect(strip.getByTestId('stat-credentials-blocked')).toHaveTextContent('2 blocked');
	});

	it('explains the blocked notes in the shared Tooltip, reachable by keyboard', () => {
		const strip = renderStrip({ ...BASE, blockedBindings: 2, fullyBlockedApis: 1 });
		for (const id of ['stat-apis-blocked', 'stat-credentials-blocked']) {
			const note = strip.getByTestId(id);
			expect(note).not.toHaveAttribute('title');
			const trigger = note.parentElement!;
			expect(trigger).toHaveAttribute('tabindex', '0');
			expect(trigger).toHaveAccessibleDescription(/no (credential with a )?rule/);
		}
	});

	it('draws a 7-day sparkline beside the call count', () => {
		const now = Math.floor(Date.now() / 1000);
		const strip = renderStrip(BASE, {
			usage: {
				total: 12,
				success: 12,
				failed: 0,
				bucketSeconds: 21_600,
				buckets: [{ ts: now - 3600, total: 12, success: 12, failed: 0 }],
			},
		});
		const svg = strip.getByTestId('stat-executions').querySelector('svg');
		expect(svg).not.toBeNull();
		expect(svg).toHaveAttribute('aria-hidden', 'true');
		expect(svg).toHaveClass('text-primary');
	});

	it('draws a muted flat baseline for an agent with no calls', () => {
		const strip = renderStrip(BASE, {
			usage: { total: 0, success: 0, failed: 0, bucketSeconds: 21_600, buckets: [] },
		});
		const svg = strip.getByTestId('stat-executions').querySelector('svg');
		expect(svg).toHaveClass('text-foreground-faint');
	});

	it('omits the activity figures for a viewer who can’t read them', () => {
		const strip = renderStrip(BASE);
		expect(strip.queryByTestId('stat-executions')).not.toBeInTheDocument();
		expect(strip.queryByTestId('stat-success-rate')).not.toBeInTheDocument();
		expect(strip.queryByTestId('stat-last-activity')).not.toBeInTheDocument();
	});

	it('prints calls, success and last used from the usage rollup', () => {
		const strip = renderStrip(BASE, {
			usage: { total: 200, success: 199, failed: 1 } as ActorUsageDetail,
			lastActivity: { at: null },
		});
		expect(strip.getByTestId('stat-executions-value')).toHaveTextContent(/^200$/);
		expect(strip.getByTestId('stat-success-rate-value')).toHaveTextContent(/^99\.5%$/);
		expect(strip.getByTestId('stat-last-activity-value')).toHaveTextContent(/^—$/);
	});

	it('an unhealthy success rate reads in the danger tone', () => {
		const strip = renderStrip(BASE, {
			usage: { total: 10, success: 5, failed: 5 } as ActorUsageDetail,
		});
		expect(strip.getByTestId('stat-success-rate-value').parentElement).toHaveClass(
			'text-danger',
		);
	});
});
