import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { Button, type ButtonVariant } from '@/shared/ui/Button';

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

	it('no variant draws a border — surfaces are raised by a tonal step', () => {
		const variants: ButtonVariant[] = [
			'primary',
			'secondary',
			'tonal',
			'outline',
			'ghost',
			'danger',
		];
		renderWithProviders(
			<div>
				{variants.map((v) => (
					<Button key={v} variant={v}>
						{v}
					</Button>
				))}
			</div>,
		);
		for (const v of variants) {
			const btn = screen.getByRole('button', { name: v });
			expect(getComputedStyle(btn).borderTopWidth).toBe('0px');
			expect(btn).not.toHaveClass('shadow-card');
			// No inset-ring edge either: a button reads by its fill alone.
			expect(btn.className).not.toMatch(/control-edge/);
		}
	});

	it('xs and icon-xs are 28px compact actions', () => {
		renderWithProviders(
			<div>
				<Button variant="tonal" size="xs">
					Add
				</Button>
				<Button variant="tonal" size="icon-xs" aria-label="Pause">
					<span aria-hidden="true">‖</span>
				</Button>
			</div>,
		);
		expect(getComputedStyle(screen.getByRole('button', { name: 'Add' })).height).toBe('28px');
		const icon = screen.getByRole('button', { name: 'Pause' });
		expect(getComputedStyle(icon).height).toBe('28px');
		expect(getComputedStyle(icon).width).toBe('28px');
	});

	it('every variant keeps AA text contrast, on the page and on a card', async () => {
		const variants: ButtonVariant[] = [
			'primary',
			'secondary',
			'tonal',
			'outline',
			'ghost',
			'danger',
		];
		const { container } = renderWithProviders(
			<div>
				{variants.map((v) => (
					<Button key={v} variant={v} size="sm">
						{`${v} page`}
					</Button>
				))}
				<div className="bg-surface-1 p-2">
					{variants.map((v) => (
						<Button key={v} variant={v} size="xs">
							{`${v} card`}
						</Button>
					))}
				</div>
			</div>,
		);
		await checkA11y(container);
	});
});
