import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { GrantAgentStatusChip } from '@/shared/ui/GrantAgentStatusChip';

describe('GrantAgentStatusChip', () => {
	it('marks an active grant on a disabled agent as dormant, with the why in the tooltip', async () => {
		const { container } = renderWithProviders(
			<GrantAgentStatusChip grantStatus="active" agentStatus="disabled" />,
		);
		// Disable/Enable vocabulary — never "deactivated".
		expect(screen.getByText('Agent disabled')).toBeInTheDocument();
		// The tooltip explains the dormancy AND why active counts exclude it,
		// naming Enable as the way back (the reversible arm).
		expect(
			screen.getByText(
				/no tokens are issued while the agent is disabled, and active-connection counts exclude it\. Enable the agent to restore the connection\./,
			),
		).toBeInTheDocument();
		await checkA11y(container);
	});

	it('labels other non-active lifecycle states from the shared vocabulary', () => {
		renderWithProviders(<GrantAgentStatusChip grantStatus="active" agentStatus="archived" />);
		expect(screen.getByText('Agent archived')).toBeInTheDocument();
		// Archive is not the reversible arm — no Enable hint.
		expect(screen.queryByText(/Enable the agent/)).not.toBeInTheDocument();
	});

	it('renders nothing for a working connection (agent active)', () => {
		renderWithProviders(<GrantAgentStatusChip grantStatus="active" agentStatus="active" />);
		expect(screen.queryByText(/Agent /)).not.toBeInTheDocument();
	});

	it('renders nothing when the API omitted the annotation', () => {
		renderWithProviders(<GrantAgentStatusChip grantStatus="active" agentStatus={null} />);
		renderWithProviders(<GrantAgentStatusChip grantStatus="active" />);
		expect(screen.queryByText(/Agent /)).not.toBeInTheDocument();
	});

	it('renders nothing on a revoked grant — its own badge already explains it', () => {
		renderWithProviders(<GrantAgentStatusChip grantStatus="revoked" agentStatus="disabled" />);
		expect(screen.queryByText('Agent disabled')).not.toBeInTheDocument();
	});

	it('normalizes an unknown agent status through the shared vocabulary (→ archived)', () => {
		renderWithProviders(
			<GrantAgentStatusChip grantStatus="active" agentStatus="totally-unknown" />,
		);
		expect(screen.getByText('Agent archived')).toBeInTheDocument();
	});
});
