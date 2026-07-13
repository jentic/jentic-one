import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { Kbd } from '@/shared/ui/Kbd';

describe('Kbd', () => {
	it('renders the key text', () => {
		renderWithProviders(<Kbd>⌘</Kbd>);
		expect(screen.getByText('⌘')).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<span>
				<Kbd>⌘</Kbd>
				<Kbd variant="solid" size="md">
					K
				</Kbd>
			</span>,
		);
		await checkA11y(container);
	});
});
