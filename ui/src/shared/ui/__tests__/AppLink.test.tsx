import type { ReactElement } from 'react';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { renderWithProviders, screen, checkA11y } from '@/__tests__/test-utils';
import { AppLink } from '@/shared/ui/AppLink';

describe('AppLink', () => {
	it('renders an internal router link', () => {
		renderWithProviders(<AppLink href="/dashboard">Dashboard</AppLink>);
		const link = screen.getByRole('link', { name: 'Dashboard' });
		expect(link).toHaveAttribute('href', '/dashboard');
	});

	it('renders an external link with safe rel/target', () => {
		renderWithProviders(<AppLink href="https://example.com">External</AppLink>);
		const link = screen.getByRole('link', { name: 'External' });
		expect(link).toHaveAttribute('href', 'https://example.com');
		expect(link).toHaveAttribute('target', '_blank');
		expect(link).toHaveAttribute('rel', 'noopener noreferrer');
	});

	it('refuses javascript: hrefs and renders an inert span', () => {
		renderWithProviders(<AppLink href="javascript:alert(1)">XSS</AppLink>);
		const el = screen.getByText('XSS');
		expect(el.tagName).toBe('SPAN');
		expect(el).toHaveAttribute('aria-disabled', 'true');
		expect(el).not.toHaveAttribute('href');
	});

	it('refuses data: and vbscript: hrefs', () => {
		renderWithProviders(
			<>
				<AppLink href="data:text/html,<script>1</script>">Data</AppLink>
				<AppLink href="vbscript:msgbox">VB</AppLink>
			</>,
		);
		expect(screen.getByText('Data').tagName).toBe('SPAN');
		expect(screen.getByText('VB').tagName).toBe('SPAN');
	});

	it('gives navigable variants a visible focus-visible ring (preflight strips the UA outline)', () => {
		renderWithProviders(
			<>
				<AppLink href="/internal">Internal</AppLink>
				<AppLink href="https://example.com">External</AppLink>
			</>,
		);
		expect(screen.getByRole('link', { name: 'Internal' }).className).toContain(
			'focus-visible:ring-2',
		);
		expect(screen.getByRole('link', { name: 'External' }).className).toContain(
			'focus-visible:ring-2',
		);
	});

	it('merges the focus ring with a caller className and skips it on the inert span', () => {
		renderWithProviders(
			<>
				<AppLink href="/internal" className="text-accent-teal">
					Styled
				</AppLink>
				<AppLink href="javascript:alert(1)">Inert</AppLink>
			</>,
		);
		const link = screen.getByRole('link', { name: 'Styled' });
		expect(link.className).toContain('text-accent-teal');
		expect(link.className).toContain('focus-visible:ring-2');
		// The XSS-guard span is non-navigable, so it carries no focus ring.
		expect(screen.getByText('Inert').className).not.toContain('focus-visible:ring-2');
	});

	it('has no critical a11y violations', async () => {
		const { container } = renderWithProviders(
			<AppLink href="/somewhere">Go somewhere</AppLink>,
		);
		await checkA11y(container);
	});
});

/**
 * Production runs under React Router with `basename="/app"` (main.tsx derives
 * the basename from Vite's `base`). Internal links are authored root-relative
 * (`/credentials`), and react-router prepends the basename at render time.
 *
 * The rest of the suite renders under a bare router (no basename), so it
 * exercises the basename-stripped values components emit but never the prepend
 * step itself — meaning a regression that dropped or corrupted the basename in
 * main.tsx would leave every other unit/mocked test green. These cases pin that
 * step: with basename "/app", a root-relative href must resolve to "/app/…".
 *
 * MemoryRouter (not BrowserRouter) is used so the initial location can be set
 * *inside* the basename — under the browser-mode test runner, window.location
 * is the harness URL, which BrowserRouter would reject as outside "/app".
 */
describe('AppLink under router basename="/app"', () => {
	function renderUnderBasename(ui: ReactElement) {
		return render(
			<MemoryRouter basename="/app" initialEntries={['/app']}>
				{ui}
			</MemoryRouter>,
		);
	}

	it('prepends the basename to a root-relative internal href', () => {
		renderUnderBasename(<AppLink href="/credentials">Credentials</AppLink>);
		expect(screen.getByRole('link', { name: 'Credentials' })).toHaveAttribute(
			'href',
			'/app/credentials',
		);
	});

	it('resolves the basename index href to the mount root', () => {
		renderUnderBasename(<AppLink href="/">Home</AppLink>);
		// react-router renders the basename root as "/app" (no trailing slash).
		expect(screen.getByRole('link', { name: 'Home' })).toHaveAttribute('href', '/app');
	});

	it('never double-prefixes and leaves external hrefs untouched', () => {
		renderUnderBasename(
			<>
				<AppLink href="/agents/agnt_1">Agent</AppLink>
				<AppLink href="https://example.com">External</AppLink>
			</>,
		);
		expect(screen.getByRole('link', { name: 'Agent' })).toHaveAttribute(
			'href',
			'/app/agents/agnt_1',
		);
		expect(screen.getByRole('link', { name: 'External' })).toHaveAttribute(
			'href',
			'https://example.com',
		);
	});
});
