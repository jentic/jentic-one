import { describe, expect, it } from 'vitest';
import { renderWithProviders, screen, within } from '@/__tests__/test-utils';
import { AgentStatStrip } from '@/modules/agents/components/flat/AgentStatStrip';
import type { ApiTileStats } from '@/modules/agents/lib/apiTiles';

const BASE: ApiTileStats = {
	configured: 1,
	needsSetup: 0,
	operations: 4,
	operationsAtLeast: false,
	operationsChecking: false,
	blocked: 0,
};

function renderStrip(access: ApiTileStats) {
	renderWithProviders(
		<AgentStatStrip
			agentName="agent-one"
			access={access}
			credentialCount={1}
			usage={null}
			lastActivity={null}
		/>,
	);
	return within(screen.getByTestId('agent-stat-strip'));
}

describe('AgentStatStrip — the reachable figure', () => {
	it('states the figure once every tile’s rules are read', () => {
		expect(renderStrip(BASE).getByTestId('stat-operations')).toHaveTextContent(
			'4 operations reachable',
		);
	});

	it('claims nothing when a tile can’t read its rules (Status unavailable)', () => {
		const strip = renderStrip({ ...BASE, operations: null });
		expect(strip.queryByTestId('stat-operations')).not.toBeInTheDocument();
		expect(strip.queryByText(/operations reachable/)).not.toBeInTheDocument();
	});

	it('holds the figure on a skeleton while a tile is still checking access', () => {
		const strip = renderStrip({ ...BASE, operationsChecking: true });
		expect(strip.queryByText(/operations reachable/)).not.toBeInTheDocument();
	});
});
