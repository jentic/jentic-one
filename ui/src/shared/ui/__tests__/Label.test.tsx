import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { Label } from '@/shared/ui/Label';

describe('Label', () => {
	it('renders associated to a control', () => {
		renderWithProviders(
			<div>
				<Label htmlFor="email">Email</Label>
				<input id="email" type="text" />
			</div>,
		);
		expect(screen.getByText('Email')).toBeInTheDocument();
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<div>
				<Label htmlFor="name" required>
					Name
				</Label>
				<input id="name" type="text" />
			</div>,
		);
		await checkA11y(container);
	});
});
