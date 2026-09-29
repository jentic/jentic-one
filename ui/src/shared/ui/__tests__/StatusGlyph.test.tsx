import { describe, it, expect } from 'vitest';
import { render, screen } from '@/__tests__/test-utils';
import { StatusGlyph } from '@/shared/ui/StatusGlyph';

describe('StatusGlyph', () => {
	it.each([
		['ok', 'Completed'],
		['fail', 'Denied'],
		['warn', 'Waiting'],
		['running', 'Running'],
		['neutral', 'Recorded'],
	] as const)('renders the %s tone with its screen-reader word', (tone, label) => {
		const { container } = render(<StatusGlyph tone={tone} label={label} />);
		const root = container.querySelector(`[data-tone="${tone}"]`);
		expect(root).toBeInTheDocument();
		expect(root).not.toHaveAttribute('aria-hidden');
		expect(screen.getByText(label)).toHaveClass('sr-only');
	});

	it.each([undefined, ''])('is decorative without a label (%j)', (label) => {
		const { container } = render(<StatusGlyph tone="ok" label={label} />);
		const root = container.querySelector('[data-tone="ok"]');
		expect(root).toHaveAttribute('aria-hidden', 'true');
		expect(root?.querySelector('.sr-only')).toBeNull();
	});
});
