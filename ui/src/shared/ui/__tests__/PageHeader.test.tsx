import { afterEach } from 'vitest';
import { page } from 'vitest/browser';
import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { PageHeader } from '@/shared/ui/PageHeader';
import { PageShell } from '@/shared/ui/PageShell';
import { Button } from '@/shared/ui/Button';

describe('PageHeader', () => {
	it('renders the title as a level-1 heading', () => {
		renderWithProviders(<PageHeader title="Dashboard" animated={false} />);
		expect(screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument();
	});

	it('renders subtitle and actions', () => {
		renderWithProviders(
			<PageHeader
				title="APIs"
				subtitle="Browse the catalog"
				actions={<Button>New API</Button>}
				animated={false}
			/>,
		);
		expect(screen.getByText('Browse the catalog')).toBeInTheDocument();
		expect(screen.getByRole('button', { name: 'New API' })).toBeInTheDocument();
	});

	it('fires action callbacks', async () => {
		const user = userEvent.setup();
		const onClick = vi.fn();
		renderWithProviders(
			<PageHeader
				title="APIs"
				actions={<Button onClick={onClick}>Action</Button>}
				animated={false}
			/>,
		);
		await user.click(screen.getByRole('button', { name: 'Action' }));
		expect(onClick).toHaveBeenCalledOnce();
	});

	it('tints the divider: an edge-to-edge accent line fading out to the band end', () => {
		const { container } = renderWithProviders(
			<PageHeader
				title="Library"
				subtitle="Everything your agents can use."
				animated={false}
			/>,
		);
		const band = container.querySelector('.page-header-band') as HTMLElement;
		const after = getComputedStyle(band, '::after');
		const b = band.getBoundingClientRect();
		// On the divider, edge to edge across the band.
		expect(after.position).toBe('absolute');
		expect(after.bottom).toBe('0px');
		expect(after.left).toBe('0px');
		expect(after.right).toBe('0px');
		expect(parseFloat(after.width)).toBeCloseTo(b.width, 0);
		// Whole-pixel band height, so the line lands crisp.
		expect(Number.isInteger(b.height)).toBe(true);
		// A gradient that ends transparent (the fade), not a solid stub.
		expect(after.backgroundImage).toContain('linear-gradient');
		expect(after.backgroundImage).toMatch(/rgba\([^)]*,\s*0\) 100%\)/);
		expect(after.backgroundSize).toContain('2px');
	});

	describe('layout', () => {
		afterEach(async () => {
			await page.viewport(1280, 900);
		});

		it('wraps its actions onto their own row at phone width instead of clipping them', async () => {
			await page.viewport(390, 844);
			renderWithProviders(
				<PageShell>
					<PageHeader
						title="Agents"
						subtitle="Approve, deny, and govern agents across their lifecycle."
						animated={false}
						actions={
							<>
								<Button size="sm">Filter agents</Button>
								<Button size="sm">New agent</Button>
								<Button size="sm">Credentials</Button>
								<Button size="sm">Help</Button>
							</>
						}
					/>
				</PageShell>,
			);
			for (const name of ['Filter agents', 'New agent', 'Credentials', 'Help']) {
				const r = screen.getByRole('button', { name }).getBoundingClientRect();
				expect(r.left).toBeGreaterThanOrEqual(0);
				expect(r.right).toBeLessThanOrEqual(window.innerWidth);
			}
			expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);
		});
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<PageHeader title="Dashboard" subtitle="Overview" animated={false} />,
		);
		await checkA11y(container);
	});
});
