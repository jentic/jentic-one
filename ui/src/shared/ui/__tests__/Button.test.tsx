import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { Button } from '@/shared/ui/Button';

describe('Button', () => {
	it('renders its children', () => {
		renderWithProviders(<Button>Click me</Button>);
		expect(screen.getByRole('button', { name: 'Click me' })).toBeInTheDocument();
	});

	it('defaults to type="button"', () => {
		renderWithProviders(<Button>Safe</Button>);
		expect(screen.getByRole('button', { name: 'Safe' })).toHaveAttribute('type', 'button');
	});

	it('fires onClick when clicked', async () => {
		const user = userEvent.setup();
		const onClick = vi.fn();
		renderWithProviders(<Button onClick={onClick}>Go</Button>);
		await user.click(screen.getByRole('button', { name: 'Go' }));
		expect(onClick).toHaveBeenCalledOnce();
	});

	it('disables and marks busy while loading', () => {
		renderWithProviders(<Button loading>Loading</Button>);
		const btn = screen.getByRole('button');
		expect(btn).toBeDisabled();
		expect(btn).toHaveAttribute('aria-busy', 'true');
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(<Button>Accessible</Button>);
		await checkA11y(container);
	});
});
