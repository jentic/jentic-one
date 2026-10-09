import { describe, it, expect, vi } from 'vitest';
import { renderWithProviders, screen, userEvent, checkA11y } from '@/__tests__/test-utils';
import { InlineAction } from '@/shared/ui/InlineAction';

describe('InlineAction', () => {
	it('is a ghost Button: a typed button that fires onClick', async () => {
		const user = userEvent.setup();
		const onClick = vi.fn();
		renderWithProviders(<InlineAction onClick={onClick}>Resume</InlineAction>);
		const btn = screen.getByRole('button', { name: 'Resume' });
		expect(btn).toHaveAttribute('type', 'button');
		expect(btn).toHaveAttribute('data-variant', 'ghost');
		await user.click(btn);
		expect(onClick).toHaveBeenCalledOnce();
	});

	it("takes the line's type and cancels its block padding, so it never moves the line", () => {
		renderWithProviders(
			<p style={{ fontSize: '11.5px', lineHeight: '16px' }}>
				<InlineAction className="px-1.5">Resume</InlineAction>
			</p>,
		);
		const s = getComputedStyle(screen.getByRole('button'));
		expect(s.fontSize).toBe('11.5px');
		expect(s.lineHeight).toBe('16px');
		expect(parseFloat(s.marginTop)).toBe(-parseFloat(s.paddingTop));
		expect(parseFloat(s.marginBottom)).toBe(-parseFloat(s.paddingBottom));
		// No press scale, no ring offset: it is part of the text, not a pad.
		expect(screen.getByRole('button').className).toMatch(/active:scale-100/);
		expect(screen.getByRole('button').className).not.toMatch(/\bh-7\b/);
	});

	it('lets the caller own weight, colour and padding', () => {
		renderWithProviders(
			<InlineAction className="text-foreground-sub px-1.5 font-bold">Resume</InlineAction>,
		);
		const cls = screen.getByRole('button').className;
		expect(cls).toMatch(/\bfont-bold\b/);
		expect(cls).not.toMatch(/\bfont-semibold\b/);
		expect(cls).toMatch(/\btext-foreground-sub\b/);
		expect(cls).not.toMatch(/\btext-muted-foreground\b/);
		expect(cls).toMatch(/\bpx-1\.5\b/);
	});

	it('disables like a Button and has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<InlineAction disabled aria-label="Resume Slack access">
				Resume
			</InlineAction>,
		);
		expect(screen.getByRole('button')).toBeDisabled();
		await checkA11y(container);
	});
});
