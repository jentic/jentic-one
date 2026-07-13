import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { RefreshButton } from '@/shared/ui/RefreshButton';

describe('RefreshButton', () => {
	it('calls onRefresh when clicked', async () => {
		const user = userEvent.setup();
		const onRefresh = vi.fn();
		renderWithProviders(<RefreshButton onRefresh={onRefresh} />);
		await user.click(screen.getByRole('button', { name: 'Refresh' }));
		expect(onRefresh).toHaveBeenCalledOnce();
	});

	it('is disabled while pending', () => {
		renderWithProviders(<RefreshButton onRefresh={() => {}} pending />);
		expect(screen.getByRole('button', { name: 'Refresh' })).toBeDisabled();
	});

	it('respects a hard disabled flag', async () => {
		const user = userEvent.setup();
		const onRefresh = vi.fn();
		renderWithProviders(<RefreshButton onRefresh={onRefresh} disabled />);
		await user.click(screen.getByRole('button', { name: 'Refresh' }));
		expect(onRefresh).not.toHaveBeenCalled();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<RefreshButton onRefresh={() => {}} />);
		await checkA11y(container);
	});
});
