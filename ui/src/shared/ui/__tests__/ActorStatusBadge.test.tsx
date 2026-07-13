import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import {
	ActorStatusBadge,
	STATUS_BADGE_VARIANT,
	STATUS_LABELS,
	toActorStatus,
} from '@/shared/ui/ActorStatusBadge';

describe('ActorStatusBadge', () => {
	it('renders the canonical capitalized label for a known status', () => {
		renderWithProviders(<ActorStatusBadge status="active" />);
		expect(screen.getByText('Active')).toBeInTheDocument();
		expect(screen.queryByText('active')).not.toBeInTheDocument();
	});

	it('uses the dedicated pending variant and label', () => {
		expect(STATUS_LABELS.pending).toBe('Pending');
		expect(STATUS_BADGE_VARIANT.pending).toBe('pending');
		renderWithProviders(<ActorStatusBadge status="pending" />);
		expect(screen.getByText('Pending')).toBeInTheDocument();
	});

	it('normalizes an unknown status to the terminal archived state', () => {
		expect(toActorStatus('totally-unknown')).toBe('archived');
		renderWithProviders(<ActorStatusBadge status="totally-unknown" />);
		expect(screen.getByText('Archived')).toBeInTheDocument();
	});

	it('has no a11y violations', async () => {
		const { container } = renderWithProviders(<ActorStatusBadge status="rejected" />);
		await checkA11y(container);
	});
});
