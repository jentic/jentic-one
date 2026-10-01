/**
 * useGithubPick — what the zero-agents landing offers a newly approved agent.
 * Driven by real-shaped `/catalog?q=github` responses: the live catalog has no
 * bare `github.com` entry, so the pick must come from GitHub's REST umbrella id.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { useGithubPick } from '@/modules/agents/lib/githubPick';
import {
	GITHUB_SEARCH_ENTRIES,
	catalogPage,
	liveGithubCatalog,
	unsyncedCatalog,
} from '@/modules/agents/mocks/githubCatalog';

function Harness() {
	const { pick, loading, catalogUnavailable } = useGithubPick();
	return (
		<output data-testid="pick">
			{loading
				? 'loading'
				: JSON.stringify({
						source: pick?.source ?? null,
						apiId: pick?.apiId ?? null,
						vendor: pick?.vendor ?? null,
						name: pick?.name ?? null,
						label: pick?.label ?? null,
						registered: pick?.registered ?? null,
						catalogUnavailable,
					})}
		</output>
	);
}

const noWorkspaceApis = http.get('/apis', () =>
	HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
);

async function settled(): Promise<Record<string, unknown>> {
	renderWithProviders(<Harness />);
	const out = screen.getByTestId('pick');
	await waitFor(() => expect(out).not.toHaveTextContent('loading'));
	return JSON.parse(out.textContent ?? '{}') as Record<string, unknown>;
}

describe('useGithubPick', () => {
	beforeEach(() => setToken('test-token'));

	it('finds GitHub REST in the live catalog via the picker search, not a bare github.com id', async () => {
		const seen: string[] = [];
		worker.use(
			noWorkspaceApis,
			http.get('/catalog', ({ request }) => {
				seen.push(new URL(request.url).searchParams.get('q') ?? '');
				return undefined;
			}),
			liveGithubCatalog,
		);
		expect(await settled()).toEqual({
			source: 'catalog',
			apiId: 'github.com/api.github.com',
			vendor: 'github.com',
			// The identity a catalog import registers — same as the picker's pick.
			name: 'github.com/api.github.com',
			label: 'GitHub',
			registered: false,
			catalogUnavailable: false,
		});
		expect(seen).toContain('github');
	});

	it('falls back to the api.github.com mirror, and never offers Enterprise Cloud or a *.github.io host', async () => {
		const [io, mirror, , , ghec] = GITHUB_SEARCH_ENTRIES;
		worker.use(
			noWorkspaceApis,
			http.get('/catalog', () => HttpResponse.json(catalogPage([io!, ghec!, mirror!]))),
		);
		expect(await settled()).toMatchObject({ apiId: 'api.github.com' });
	});

	it('offers nothing when the search only finds Enterprise Cloud and github.io hosts', async () => {
		const [io, , , , ghec] = GITHUB_SEARCH_ENTRIES;
		worker.use(
			noWorkspaceApis,
			http.get('/catalog', () => HttpResponse.json(catalogPage([io!, ghec!]))),
		);
		expect(await settled()).toMatchObject({
			source: null,
			catalogUnavailable: false,
		});
	});

	it('prefers a GitHub API already in the workspace', async () => {
		worker.use(
			liveGithubCatalog,
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						{
							api: {
								vendor: 'github-com',
								name: 'github-com-api-github-com',
								version: '1.1.4',
							},
							catalog_api_id: 'github.com/api.github.com',
							display_name: null,
							description: null,
							_links: {},
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		expect(await settled()).toMatchObject({
			source: 'local',
			vendor: 'github-com',
			name: 'github-com-api-github-com',
		});
	});

	it('a GitHub API in the workspace settles the pick without waiting for the catalog', async () => {
		let release!: () => void;
		const gate = new Promise<void>((resolve) => {
			release = resolve;
		});
		worker.use(
			http.get('/catalog', async () => {
				await gate;
				return undefined;
			}),
			http.get('/apis', () =>
				HttpResponse.json({
					data: [
						{
							api: { vendor: 'github-com', name: 'github-rest', version: '1.0.0' },
							catalog_api_id: 'github.com/api.github.com',
							display_name: null,
							description: null,
							_links: {},
						},
					],
					has_more: false,
					next_cursor: null,
				}),
			),
		);
		try {
			expect(await settled()).toMatchObject({ source: 'local', vendor: 'github-com' });
		} finally {
			release();
		}
	});

	it('reports an unsynced (empty) catalog as unavailable', async () => {
		worker.use(noWorkspaceApis, unsyncedCatalog);
		expect(await settled()).toMatchObject({ source: null, catalogUnavailable: true });
	});

	it('reports a failing catalog as unavailable', async () => {
		worker.use(
			noWorkspaceApis,
			http.get('/catalog', () =>
				HttpResponse.json(
					{ type: 'catalog_unavailable', status: 503, title: 'Unavailable' },
					{ status: 503 },
				),
			),
		);
		expect(await settled()).toMatchObject({ source: null, catalogUnavailable: true });
	});
});
