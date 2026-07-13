import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { BackButton } from '@/shared/ui/BackButton';

describe('BackButton', () => {
	it('renders a history-aware button by default', () => {
		renderWithProviders(<BackButton to="/apis" label="Back to APIs" />);
		const btn = screen.getByTestId('back-button');
		expect(btn.tagName).toBe('BUTTON');
		expect(btn).toHaveTextContent('Back to APIs');
	});

	it('renders a link when useHistory is false', () => {
		renderWithProviders(<BackButton to="/apis" label="Back to APIs" useHistory={false} />);
		const link = screen.getByTestId('back-button');
		expect(link.tagName).toBe('A');
		expect(link).toHaveAttribute('href', '/apis');
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<BackButton to="/apis" label="Back to APIs" />);
		await checkA11y(container);
	});
});
