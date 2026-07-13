import { renderWithProviders, screen, userEvent, waitFor, checkA11y } from '@/__tests__/test-utils';
import { CopyButton } from '@/shared/ui/CopyButton';

describe('CopyButton', () => {
	it('copies the value and shows feedback', async () => {
		const user = userEvent.setup();
		renderWithProviders(<CopyButton value="secret-key" label="Copy" />);
		await user.click(screen.getByRole('button', { name: 'Copy' }));
		// Browser-mode Chromium grants clipboard-write in the test context, so
		// the real copy resolves and the button swaps to its success state.
		// We assert the user-visible feedback rather than spying on the
		// read-only `navigator.clipboard` global (which can't be reliably
		// shadowed here). Reaching the success branch proves the copy
		// (`await navigator.clipboard.writeText(value)`) resolved.
		await waitFor(() => {
			expect(screen.getByText('Copied!')).toBeInTheDocument();
		});
	});

	it('renders icon-only when no label is provided', () => {
		renderWithProviders(<CopyButton value="abc" />);
		const button = screen.getByRole('button', { name: 'Copy to clipboard' });
		expect(button.querySelector('svg')).toBeTruthy();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<CopyButton value="test" label="Copy Key" />);
		await checkA11y(container);
	});
});
