import { describe, it, expect } from 'vitest';
import { render, screen } from '@/__tests__/test-utils';
import { VendorMark } from '@/shared/ui/VendorMark';
import { AGENT_MARK_SLUGS, VENDOR_MARKS, isVendorMarkSlug } from '@/shared/ui/vendorMarks';
import { AgentMark } from '@/shared/ui/AgentMark';

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

	it('ships exactly GitHub and the AI agent marks, each a single path with a hex colour', () => {
		expect(Object.keys(VENDOR_MARKS).sort()).toEqual(
			['github', 'claude', 'openai', 'cursor', 'googlegemini'].sort(),
		);
		for (const mark of Object.values(VENDOR_MARKS)) {
			expect(mark.hex).toMatch(/^#[0-9A-F]{6}$/);
			// An absolute or relative moveto, as simple-icons ships it.
			expect(mark.path).toMatch(/^[Mm]/);
		}
		expect(isVendorMarkSlug('github')).toBe(true);
		expect(isVendorMarkSlug('toString')).toBe(false);
	});
});

describe('AgentMark', () => {
	it('clusters the AI agent marks, and has no OpenClaw mark to fake', () => {
		const { container } = render(<AgentMark label="Your AI agent" />);
		expect(screen.getByRole('img', { name: 'Your AI agent' })).toBeInTheDocument();
		expect(
			[...container.querySelectorAll('[data-vendor-mark]')].map((el) =>
				el.getAttribute('data-vendor-mark'),
			),
		).toEqual([...AGENT_MARK_SLUGS]);
		expect(AGENT_MARK_SLUGS).not.toContain('openclaw');
	});
});
