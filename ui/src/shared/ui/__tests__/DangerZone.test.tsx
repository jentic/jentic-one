import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import userEvent from '@testing-library/user-event';
import { vi } from 'vitest';
import { DangerZone, type DangerZoneAction } from '@/shared/ui/DangerZone';

const actions: DangerZoneAction[] = [
	{
		key: 'disable',
		title: 'Disable agent',
		description: 'Reversible — you can re-enable it later.',
		buttonLabel: 'Disable',
		ariaLabel: 'Disable support-agent',
		emphasis: 'outline',
	},
	{
		key: 'archive',
		title: 'Archive agent',
		description: 'This cannot be undone.',
		buttonLabel: 'Archive',
		ariaLabel: 'Archive support-agent',
	},
];

describe('DangerZone', () => {
	it('renders one row per action and hands the key back on click', async () => {
		const user = userEvent.setup();
		const onAction = vi.fn();
		renderWithProviders(<DangerZone actions={actions} onAction={onAction} />);
		expect(screen.getByText('Danger zone')).toBeInTheDocument();
		expect(screen.getByText('Disable agent')).toBeInTheDocument();
		expect(screen.getByText('Archive agent')).toBeInTheDocument();
		await user.click(screen.getByRole('button', { name: 'Archive support-agent' }));
		expect(onAction).toHaveBeenCalledWith('archive');
	});

	it('disables every button while a mutation is pending', () => {
		renderWithProviders(<DangerZone actions={actions} pending onAction={() => {}} />);
		expect(screen.getByRole('button', { name: 'Disable support-agent' })).toBeDisabled();
		expect(screen.getByRole('button', { name: 'Archive support-agent' })).toBeDisabled();
	});

	it('renders nothing when no destructive verb applies', () => {
		const { container } = renderWithProviders(<DangerZone actions={[]} onAction={() => {}} />);
		expect(container).toBeEmptyDOMElement();
	});

	it('is accessible', async () => {
		const { container } = renderWithProviders(
			<DangerZone actions={actions} onAction={() => {}} />,
		);
		await checkA11y(container);
	});
});
