import { renderWithProviders, checkA11y } from '@/__tests__/test-utils';
import { VendorIcon } from '@/shared/ui/VendorIcon';

describe('VendorIcon', () => {
	it('renders two-letter initials from the name in the pastel tile', () => {
		const { container } = renderWithProviders(<VendorIcon name="Stripe" />);
		expect(container.firstElementChild).toHaveTextContent('ST');
	});

	it('strips non-alphanumerics before taking initials', () => {
		const { container } = renderWithProviders(<VendorIcon name="big-api" />);
		// Hyphen is dropped, so the first two *alphanumeric* chars are "BI".
		expect(container.firstElementChild).toHaveTextContent('BI');
	});

	it('falls back to "??" when the name has no alphanumerics', () => {
		const { container } = renderWithProviders(<VendorIcon name="---" />);
		expect(container.firstElementChild).toHaveTextContent('??');
	});

	const toneOf = (root: Element | null) => root?.getAttribute('data-tone');

	it('is deterministic — the same seed always picks the same tone', () => {
		const { container: a } = renderWithProviders(<VendorIcon name="alpha" vendor="acme" />);
		const { container: b } = renderWithProviders(<VendorIcon name="beta" vendor="acme" />);
		// Same vendor seed → same tone even though the names differ.
		expect(toneOf(a.firstElementChild)).toBe(toneOf(b.firstElementChild));
	});

	it('is a flat pastel tile with dark initials — no gradient, no white text', () => {
		const { container } = renderWithProviders(<VendorIcon name="Stripe" vendor="stripe.com" />);
		const el = container.firstElementChild as HTMLElement;
		expect(el.className).not.toContain('bg-gradient');
		expect(el.className).not.toContain('text-white');
		expect(el.className).toContain('font-heading');
		expect(el.style.backgroundColor).toMatch(/--avatar-\d-bg/);
		expect(el.style.color).toMatch(/--avatar-\d-fg/);
	});

	it('seeds the tone from `vendor` in preference to `name`', () => {
		// vendor present → name is ignored for the seed.
		const { container: withVendor } = renderWithProviders(
			<VendorIcon name="zzz" vendor="acme" />,
		);
		const { container: nameSeed } = renderWithProviders(<VendorIcon name="acme" />);
		// vendor "acme" and name "acme" hash to the same tone.
		expect(toneOf(withVendor.firstElementChild)).toBe(toneOf(nameSeed.firstElementChild));
	});

	it('renders the real logo (decorative img) when an iconUrl is provided', () => {
		const { container } = renderWithProviders(
			<VendorIcon name="Stripe" iconUrl="https://cdn.example/stripe.png" />,
		);
		const img = container.querySelector('img');
		expect(img).not.toBeNull();
		expect(img).toHaveAttribute('src', 'https://cdn.example/stripe.png');
		// Decorative — empty alt + aria-hidden so it's skipped by AT.
		expect(img).toHaveAttribute('alt', '');
		expect(img).toHaveAttribute('aria-hidden', 'true');
		expect(container.textContent).toBe('');
	});

	it('applies size-specific box classes', () => {
		const { container } = renderWithProviders(<VendorIcon name="Stripe" size="lg" />);
		// lg = 44px (sheet / hub headers).
		expect(container.firstElementChild?.className).toContain('h-11');
		expect(container.firstElementChild?.className).toContain('w-11');
	});

	it('merges a caller-supplied className', () => {
		const { container } = renderWithProviders(<VendorIcon name="Stripe" className="ring-2" />);
		expect(container.firstElementChild?.className).toContain('ring-2');
	});

	it('has no a11y violations', async () => {
		const { container } = renderWithProviders(<VendorIcon name="Stripe" vendor="stripe.com" />);
		await checkA11y(container);
	});
});
