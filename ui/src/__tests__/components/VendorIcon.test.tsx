import { render, screen } from '@testing-library/react';
import { VendorIcon } from '@/components/discovery/VendorIcon';

describe('VendorIcon', () => {
	it('renders the first two letters as initials', () => {
		render(<VendorIcon name="Stripe" />);
		expect(screen.getByText('ST')).toBeInTheDocument();
	});

	it('is uppercase regardless of input case', () => {
		render(<VendorIcon name="github" />);
		expect(screen.getByText('GI')).toBeInTheDocument();
	});

	it('strips non-alphanumeric characters before slicing', () => {
		render(<VendorIcon name="@my-api" />);
		expect(screen.getByText('MY')).toBeInTheDocument();
	});

	it('is deterministic — same name always produces the same initials', () => {
		const { rerender } = render(<VendorIcon name="Acme Corp" />);
		const first = screen.getByText('AC');

		rerender(<VendorIcon name="Acme Corp" />);
		expect(screen.getByText('AC')).toBe(first);
	});

	it('is aria-hidden so screen readers skip the decorative icon', () => {
		const { container } = render(<VendorIcon name="Stripe" />);
		expect(container.firstChild).toHaveAttribute('aria-hidden', 'true');
	});

	// Size scale is intentionally tighter than `jentic-webapp` (32/40/48 vs
	// 40/48/64) — Discover cards in mini have less vertical room. The radius
	// scales proportionally via arbitrary values so the *visual* roundness
	// matches webapp at ~25 % radius:side. If you change either, bump the
	// `radius` field in the SIZE map alongside.
	it('applies size=sm classes when size prop is sm', () => {
		const { container } = render(<VendorIcon name="Stripe" size="sm" />);
		expect((container.firstChild as HTMLElement).className).toContain('h-8');
	});

	it('applies size=lg classes when size prop is lg', () => {
		const { container } = render(<VendorIcon name="Stripe" size="lg" />);
		expect((container.firstChild as HTMLElement).className).toContain('h-12');
	});

	it('uses a recognised brand background when vendor matches the registry', () => {
		// Stripe's brand purple — proves the registry path is being hit
		// rather than the gradient fallback. Inline style avoids depending
		// on a specific Tailwind colour class.
		const { container } = render(<VendorIcon name="Stripe" vendor="stripe.com" />);
		const root = container.firstChild as HTMLElement;
		expect(root.style.backgroundColor).toMatch(/rgb\(99, 91, 255\)/i);
	});

	it('auto-derives a slug from a 2-label domain (vendor not in registry)', () => {
		// `linear.app` isn't in the registry pre-population (until it is),
		// but the SLD `linear` is a valid Simple Icons slug. The component
		// should render the brand SVG path, not the initials fallback.
		const { container } = render(<VendorIcon name="Linear" vendor="someunknown.app" />);
		const img = container.querySelector('img');
		expect(img).not.toBeNull();
		expect(img?.getAttribute('src')).toContain('someunknown.svg');
	});

	it('skips auto-derive for non-2-label hosts (e.g. github.io pages)', () => {
		// `0xerr0r.github.io` is a personal GitHub Pages site, NOT GitHub the
		// company — must NOT render the GitHub brand logo. Falls back to
		// gradient + initials.
		const { container } = render(<VendorIcon name="0xerr0r" vendor="0xerr0r.github.io" />);
		expect(container.querySelector('img')).toBeNull();
		expect(screen.getByText('0X')).toBeInTheDocument();
	});
});
