import { describe, it, expect } from 'vitest';
import { render, screen } from '@/__tests__/test-utils';
import { VendorMark } from '@/shared/ui/VendorMark';
import { VENDOR_MARKS, isVendorMarkSlug } from '@/shared/ui/vendorMarks';

describe('VendorMark', () => {
	it('renders a known slug as a decorative brand-coloured SVG', () => {
		const { container } = render(<VendorMark slug="github" />);
		const tile = container.querySelector('[data-vendor-mark="github"]');
		expect(tile).toHaveAttribute('aria-hidden', 'true');
		const svg = tile?.querySelector('svg');
		expect(svg).toHaveAttribute('fill', VENDOR_MARKS.github.hex);
		expect(svg?.querySelector('path')).toHaveAttribute('d', VENDOR_MARKS.github.path);
	});

	it('names the mark when it stands alone', () => {
		render(<VendorMark slug="github" label="GitHub" />);
		expect(screen.getByRole('img', { name: 'GitHub' })).toBeInTheDocument();
	});

	it('falls back to a neutral initial for an unknown slug', () => {
		const { container } = render(<VendorMark slug="acme" />);
		const tile = container.querySelector('[data-vendor-mark="fallback"]');
		expect(tile).toHaveTextContent('a');
		expect(tile?.querySelector('svg')).toBeNull();
	});

	it('ships exactly the GitHub mark, a single path with a hex colour', () => {
		expect(Object.keys(VENDOR_MARKS)).toEqual(['github']);
		for (const mark of Object.values(VENDOR_MARKS)) {
			expect(mark.hex).toMatch(/^#[0-9A-F]{6}$/);
			// An absolute or relative moveto, as simple-icons ships it.
			expect(mark.path).toMatch(/^[Mm]/);
		}
		expect(isVendorMarkSlug('github')).toBe(true);
		expect(isVendorMarkSlug('toString')).toBe(false);
	});
});
