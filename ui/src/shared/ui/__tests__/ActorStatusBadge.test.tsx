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

	it('tells Pending and Disabled apart without the word: filled vs hollow dot', () => {
		renderWithProviders(
			<>
				<ActorStatusBadge status="pending" />
				<ActorStatusBadge status="disabled" />
			</>,
		);
		const dotOf = (name: string) =>
			screen.getByText(name).querySelector<HTMLElement>('span[aria-hidden="true"]')!;
		const pending = getComputedStyle(dotOf('Pending'));
		const disabled = getComputedStyle(dotOf('Disabled'));
		expect(pending.backgroundColor).not.toBe('rgba(0, 0, 0, 0)');
		expect(disabled.backgroundColor).toBe('rgba(0, 0, 0, 0)');
		expect(disabled.borderTopWidth).toBe('1px');
		expect(disabled.borderTopColor).not.toBe(pending.backgroundColor);
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
