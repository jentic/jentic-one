import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { PageShell } from '@/shared/ui/PageShell';

describe('PageShell', () => {
	it('renders its children', () => {
		renderWithProviders(
			<PageShell>
				<h1>Page content</h1>
			</PageShell>,
		);
		expect(screen.getByRole('heading', { name: 'Page content' })).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<PageShell width="form">
				<p>Form page</p>
			</PageShell>,
		);
		await checkA11y(container);
	});
});
