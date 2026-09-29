import { describe, expect, it } from 'vitest';
import { useLocation, useRoutes } from 'react-router';
import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import { ROUTE_PATHS } from '@/shared/app/routes';
import { discoverRoutes } from '@/modules/discover/routes';
import { workspaceRoutes } from '@/modules/workspace/routes';

/**
 * The Library's retired URLs (`/discover`, `/workspace`, `/workspace/:v/:n/:ver`)
 * must land on their Library equivalents with the whole deep link intact —
 * path params re-encoded, search string and hash kept — and the hub URL
 * builder must produce exactly the shape the hub route reads back.
 */

function LocationProbe() {
	const { pathname, search, hash } = useLocation();
	return <div data-testid="location">{`${pathname}${search}${hash}`}</div>;
}

/** The module routes with their pages stubbed (their data needs aren't under test). */
function Harness() {
	const stub = (label: string) => <h1>{label}</h1>;
	return useRoutes(
		[...discoverRoutes, ...workspaceRoutes].map((r) =>
			r.path === 'library'
				? { ...r, path: '/library', element: stub('Catalog') }
				: r.path === 'library/workspace'
					? { ...r, path: '/library/workspace', element: stub('Workspace') }
					: r.path === 'library/workspace/:vendor/:name/:version'
						? { ...r, path: `/${r.path}`, element: stub('Hub') }
						: { ...r, path: `/${r.path}` },
		),
	);
}

function renderAt(route: string) {
	return renderWithProviders(
		<>
			<Harness />
			<LocationProbe />
		</>,
		{ route },
	);
}

async function landedAt(heading: string): Promise<string> {
	await screen.findByRole('heading', { name: heading });
	return screen.getByTestId('location').textContent ?? '';
}

describe('Library retired-route redirects', () => {
	it('/discover → the catalog, keeping search and hash', async () => {
		renderAt('/discover?q=stripe&filter=outdated#top');
		expect(await landedAt('Catalog')).toBe('/library?q=stripe&filter=outdated#top');
	});

	it('/workspace → the Workspace view, keeping ?import=1 / ?status=', async () => {
		renderAt('/workspace?import=1&status=draft#grid');
		expect(await landedAt('Workspace')).toBe('/library/workspace?import=1&status=draft#grid');
	});

	it('/workspace/:v/:n/:ver → the hub, re-encoding each segment and keeping ?tab=', async () => {
		renderAt('/workspace/slack.com/web%20api/1.0%2Fbeta?tab=versions#overlays');
		expect(await landedAt('Hub')).toBe(
			'/library/workspace/slack.com/web%20api/1.0%2Fbeta?tab=versions#overlays',
		);
	});
});

describe('ROUTE_PATHS.workspaceApiHub', () => {
	const ref = { vendor: 'slack.com', name: 'web api', version: '1.0/beta' };

	it('percent-encodes each identity segment', () => {
		expect(ROUTE_PATHS.workspaceApiHub(ref)).toBe(
			'/library/workspace/slack.com/web%20api/1.0%2Fbeta',
		);
	});

	it('adds ?tab= for non-default tabs only, and ?credential=new on request', () => {
		expect(ROUTE_PATHS.workspaceApiHub(ref, 'overview')).not.toContain('?');
		expect(ROUTE_PATHS.workspaceApiHub(ref, 'versions')).toMatch(/\?tab=versions$/);
		expect(ROUTE_PATHS.workspaceApiHub(ref, 'overview', { addCredential: true })).toMatch(
			/\?credential=new$/,
		);
	});

	it('builds a URL the hub route matches back to the same triple', async () => {
		renderAt(ROUTE_PATHS.workspaceApiHub(ref, 'versions'));
		await waitFor(() =>
			expect(screen.getByTestId('location').textContent).toBe(
				'/library/workspace/slack.com/web%20api/1.0%2Fbeta?tab=versions',
			),
		);
		expect(screen.getByRole('heading', { name: 'Hub' })).toBeInTheDocument();
	});
});
