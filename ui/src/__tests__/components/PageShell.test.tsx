import { render } from '@testing-library/react';
import { PageShell } from '@/components/layout/PageShell';

function getShell(container: HTMLElement) {
	return container.firstElementChild as HTMLElement;
}

describe('PageShell', () => {
	it('renders its children and applies the shared page gutter', () => {
		const { container, getByText } = render(
			<PageShell>
				<p>hello</p>
			</PageShell>,
		);
		expect(getByText('hello')).toBeInTheDocument();
		const shell = getShell(container);
		// `px-page-gutter` is the Tailwind 4 utility generated from
		// `--spacing-page-gutter` in index.css; matches jentic-webapp.
		expect(shell.className).toContain('px-page-gutter');
		expect(shell.className).toContain('w-full');
	});

	it('defaults to the wide variant (no max-width cap)', () => {
		const { container } = render(
			<PageShell>
				<p>x</p>
			</PageShell>,
		);
		// Wide is uncapped so it stays edge-to-edge with the full-bleed PageHeader.
		expect(getShell(container).className).not.toContain('max-w-');
	});

	it('centres and caps reading variant at max-w-4xl', () => {
		const { container } = render(
			<PageShell width="reading">
				<p>x</p>
			</PageShell>,
		);
		expect(getShell(container).className).toContain('max-w-4xl');
		expect(getShell(container).className).toContain('mx-auto');
	});

	it('centres and caps form variant at max-w-2xl', () => {
		const { container } = render(
			<PageShell width="form">
				<p>x</p>
			</PageShell>,
		);
		expect(getShell(container).className).toContain('max-w-2xl');
		expect(getShell(container).className).toContain('mx-auto');
	});

	it('applies the default space-y-6 vertical rhythm', () => {
		const { container } = render(
			<PageShell>
				<p>x</p>
			</PageShell>,
		);
		expect(getShell(container).className).toContain('space-y-6');
	});

	it('accepts a custom spacing class', () => {
		const { container } = render(
			<PageShell spacing="space-y-2">
				<p>x</p>
			</PageShell>,
		);
		expect(getShell(container).className).toContain('space-y-2');
		expect(getShell(container).className).not.toContain('space-y-6');
	});

	it('appends a custom className', () => {
		const { container } = render(
			<PageShell className="custom-shell">
				<p>x</p>
			</PageShell>,
		);
		expect(getShell(container).className).toContain('custom-shell');
	});
});
