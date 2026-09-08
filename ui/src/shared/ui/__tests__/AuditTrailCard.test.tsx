import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { AuditTrailCard, type AuditTrailEntry } from '@/shared/ui/AuditTrailCard';

const entries: AuditTrailEntry[] = [
	{
		id: 'a1',
		action: 'agent.approve',
		actorId: 'user_1',
		actorType: 'user',
		occurredAt: new Date(Date.now() - 60_000).toISOString(),
	},
	{
		id: 'a2',
		action: 'toolkit.suspend',
		actorId: null,
		actorType: null,
		reason: 'incident response',
		occurredAt: new Date(Date.now() - 3_600_000).toISOString(),
	},
];

describe('AuditTrailCard', () => {
	it('renders the "Recent changes" heading, caption, and entry rows', () => {
		renderWithProviders(
			<AuditTrailCard
				entries={entries}
				caption="Lifecycle events · admin only"
				emptyMessage="No recorded changes."
			/>,
		);
		expect(screen.getByText('Recent changes')).toBeInTheDocument();
		expect(screen.getByText('Lifecycle events · admin only')).toBeInTheDocument();
		expect(screen.getByText('agent.approve')).toBeInTheDocument();
		expect(screen.getByText('toolkit.suspend')).toBeInTheDocument();
		// Reason surfaces after the (system) actor; system fallback shown when
		// there is no actor id.
		expect(screen.getByText(/incident response/)).toBeInTheDocument();
		expect(screen.getByText(/system/)).toBeInTheDocument();
	});

	it('shows the dashed empty state when there are no entries', () => {
		renderWithProviders(<AuditTrailCard entries={[]} emptyMessage="Nothing recorded yet." />);
		expect(screen.getByText('Nothing recorded yet.')).toBeInTheDocument();
	});

	it('shows the error state instead of rows', () => {
		renderWithProviders(
			<AuditTrailCard
				entries={entries}
				isError
				errorMessage="Failed to load the audit log."
				emptyMessage="Nothing recorded yet."
			/>,
		);
		expect(screen.getByText('Failed to load the audit log.')).toBeInTheDocument();
		expect(screen.queryByText('Nothing recorded yet.')).not.toBeInTheDocument();
		// Stale cache entries must not render behind the error alert.
		expect(screen.queryByText('agent.approve')).not.toBeInTheDocument();
	});

	it('suppresses rows and the empty state while loading', () => {
		renderWithProviders(
			<AuditTrailCard entries={entries} isLoading emptyMessage="Nothing recorded yet." />,
		);
		expect(screen.getByText('Recent changes')).toBeInTheDocument();
		expect(screen.queryByText('agent.approve')).not.toBeInTheDocument();
		expect(screen.queryByText('Nothing recorded yet.')).not.toBeInTheDocument();
	});

	it('is accessible', async () => {
		const { container } = renderWithProviders(
			<AuditTrailCard entries={entries} emptyMessage="No recorded changes." />,
		);
		await checkA11y(container);
	});
});
