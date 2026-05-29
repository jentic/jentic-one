import { render, screen } from '@testing-library/react';
import { PageHeader } from '@/components/ui/PageHeader';

// Disable animation in all tests so framer-motion doesn't interfere.
const noAnim = { animated: false } as const;

describe('PageHeader', () => {
	it('renders title as h1 heading', () => {
		render(<PageHeader title="Dashboard" {...noAnim} />);
		expect(screen.getByRole('heading', { name: 'Dashboard', level: 1 })).toBeInTheDocument();
	});

	it('renders subtitle when provided', () => {
		render(<PageHeader title="Dashboard" subtitle="Overview of your system." {...noAnim} />);
		expect(screen.getByText('Overview of your system.')).toBeInTheDocument();
	});

	it('does not render subtitle when omitted', () => {
		const { container } = render(<PageHeader title="Dashboard" {...noAnim} />);
		expect(container.querySelector('p')).toBeNull();
	});

	it('renders actions slot', () => {
		render(<PageHeader title="Dashboard" actions={<button>Export</button>} {...noAnim} />);
		expect(screen.getByRole('button', { name: 'Export' })).toBeInTheDocument();
	});

	it('applies animated motion wrapper by default (animated=true)', () => {
		// When animated, framer-motion renders a div wrapper around content.
		// We just assert the heading is still reachable (motion doesn't swallow it).
		render(<PageHeader title="Animated" />);
		expect(screen.getByRole('heading', { name: 'Animated', level: 1 })).toBeInTheDocument();
	});

	it('renders the full-bleed gradient band', () => {
		const { container } = render(<PageHeader title="Test" {...noAnim} />);
		// Outer band has negative margins to escape the page gutter. The
		// gutter value is owned by the Tailwind theme (`--spacing-page-gutter`)
		// so the utility name is `-mx-page-gutter`, not a hardcoded `-mx-N`.
		const band = container.firstChild as HTMLElement;
		expect(band.className).toContain('-mx-page-gutter');
		expect(band.className).toContain('bg-gradient-to-b');
	});
});
