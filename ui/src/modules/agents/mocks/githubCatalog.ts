/**
 * Real-shaped public-catalog responses for GitHub, as a live instance returns
 * them for `GET /catalog?q=github` (captured from a synced local catalog of
 * 6,317 entries). The upstream catalog has NO bare `github.com` entry: GitHub's
 * REST API is the umbrella id `github.com/api.github.com` (mirrored as
 * `api.github.com`), next to Enterprise Cloud (`github.com/ghec`) and unrelated
 * `*.github.io` hosts that also match the word. Tests `worker.use` these so the
 * landing is exercised against what the backend actually sends, not the
 * simplified `github.com` entry in the shared catalog mock.
 */
import { http, HttpResponse } from 'msw';

const RAW = 'https://raw.githubusercontent.com/jentic/jentic-public-apis/refs/heads/main';
const TREE = 'https://github.com/jentic/jentic-public-apis/tree/main';

function entry(apiId: string, vendor: string, dir: string, version: string) {
	const path = `apis/openapi/${dir}`;
	return {
		api_id: apiId,
		vendor,
		path,
		spec_url: `${RAW}/${path}/${version}/openapi.json`,
		registered: false,
		update_available: false,
		_links: {
			self: `/catalog/${apiId}`,
			operations: `/catalog/${apiId}/operations`,
			import: `/catalog/${apiId}:import`,
			github: `${TREE}/${path}`,
		},
	};
}

/** The live `q=github` result set (abridged to one row of each kind), in the
 * backend's search order. */
export const GITHUB_SEARCH_ENTRIES = [
	entry('0xerr0r.github.io', 'github.io', '0xerr0r.github.io/main', '1.0'),
	entry('api.github.com', 'github.com', 'api.github.com/main', '1.1.4'),
	entry('bullhorn.github.io', 'github.io', 'bullhorn.github.io/main', '1.0.0'),
	entry('github.com/api.github.com', 'github.com', 'github.com/api.github.com', '1.1.4'),
	entry('github.com/ghec', 'github.com', 'github.com/ghec', '1.1.4'),
];

export function catalogPage(data: ReturnType<typeof entry>[], total = 6317) {
	return {
		data,
		catalog_total: total,
		registered_count: 0,
		outdated_count: 0,
		manifest_age_seconds: 120,
		has_more: false,
		next_cursor: null,
	};
}

/** `/catalog` answering like a synced live instance: `q` filters by substring. */
export const liveGithubCatalog = http.get('/catalog', ({ request }) => {
	const q = (new URL(request.url).searchParams.get('q') ?? '').trim().toLowerCase();
	return HttpResponse.json(
		catalogPage(q ? GITHUB_SEARCH_ENTRIES.filter((e) => e.api_id.includes(q)) : []),
	);
});

/** `/catalog` on an instance whose manifest never synced (offline, or egress
 * blocked): an empty snapshot, not an error. */
export const unsyncedCatalog = http.get('/catalog', () => HttpResponse.json(catalogPage([], 0)));
