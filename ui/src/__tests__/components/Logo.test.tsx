import { render, screen } from '@testing-library/react';
import { JenticLogo } from '@/components/ui/Logo';

describe('JenticLogo', () => {
	it('renders the SVG and "Mini" badge', () => {
		render(<JenticLogo />);
		expect(screen.getByText('Mini')).toBeInTheDocument();
		const svg = document.querySelector('svg');
		expect(svg).toBeInTheDocument();
	});

	it('applies custom className to the wrapper', () => {
		// `JenticLogo` wraps the SVG + "Mini" badge in a flex container; the
		// `className` prop is appended to that wrapper so callers can adjust
		// spacing / opacity / layout of the whole unit. The webapp's
		// standalone `SvgLogo` puts it on the <svg> directly — divergent on
		// purpose because Mini needs the badge alongside the wordmark.
		const { container } = render(<JenticLogo className="opacity-50" />);
		const wrapper = container.firstElementChild as HTMLElement;
		expect(wrapper.className).toContain('opacity-50');
	});

	it('SVG has aria-hidden for decorative usage', () => {
		render(<JenticLogo />);
		const svg = document.querySelector('svg')!;
		expect(svg.getAttribute('aria-hidden')).toBe('true');
	});

	it('defaults to width 77 and height 24', () => {
		render(<JenticLogo />);
		const svg = document.querySelector('svg')!;
		expect(svg.getAttribute('width')).toBe('77');
		expect(svg.getAttribute('height')).toBe('24');
	});

	it('accepts custom width and height props', () => {
		render(<JenticLogo width={120} height={40} />);
		const svg = document.querySelector('svg')!;
		expect(svg.getAttribute('width')).toBe('120');
		expect(svg.getAttribute('height')).toBe('40');
	});
});
