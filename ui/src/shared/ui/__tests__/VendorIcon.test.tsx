import { renderWithProviders, checkA11y } from '@/__tests__/test-utils';
import { VendorIcon } from '@/shared/ui/VendorIcon';

describe('VendorIcon', () => {
	it('renders two-letter initials from the name in the gradient fallback', () => {
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

	it('is deterministic — the same seed always picks the same gradient', () => {
		const { container: a } = renderWithProviders(<VendorIcon name="alpha" vendor="acme" />);
		const { container: b } = renderWithProviders(<VendorIcon name="beta" vendor="acme" />);
		const gradientOf = (root: Element | null) =>
			(root?.className ?? '').split(' ').find((c) => c.startsWith('from-'));
		// Same vendor seed → same gradient even though the names differ.
		expect(gradientOf(a.firstElementChild)).toBe(gradientOf(b.firstElementChild));
	});

	it('seeds the gradient from `vendor` in preference to `name`', () => {
		const gradientOf = (root: Element | null) =>
			(root?.className ?? '').split(' ').find((c) => c.startsWith('from-'));
		// vendor present → name is ignored for the seed.
		const { container: withVendor } = renderWithProviders(
			<VendorIcon name="zzz" vendor="acme" />,
		);
		const { container: nameSeed } = renderWithProviders(<VendorIcon name="acme" />);
		// vendor "acme" and name "acme" hash to the same gradient; "zzz" alone would not.
		expect(gradientOf(withVendor.firstElementChild)).toBe(
			gradientOf(nameSeed.firstElementChild),
		);
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
		expect(container.firstElementChild?.className).toContain('h-12');
		expect(container.firstElementChild?.className).toContain('w-12');
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
