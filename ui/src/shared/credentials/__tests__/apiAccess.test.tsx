import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { makeMockCredential } from '@/shared/credentials/mocks/handlers';
import {
	agentsExhaustive,
	useAgentAccess,
	useApiAccessIndex,
} from '@/shared/credentials/api/apiAccess';
import { useAgentFigures } from '@/shared/credentials/api/apiHealth';
import type { Credential } from '@/shared/credentials/api';

/**
 * The one credential ↔ API rule (`apiScopeCovers` AND active) and the lazy,
 * bounded agent reads behind "agents with access": deduped across
 * credentials, honest about failures and the read cap, and issued only for the
 * credentials a surface asks about.
 */

const STRIPE = { vendor: 'stripe', name: 'stripe-api', version: '2024-01-01' };
const SLACK = { vendor: 'slack', name: 'web-api', version: '1' };

function agent(id: string) {
	return {
		agent_id: id,
		agent_name: id,
		status: 'active',
		suspended: false,
		bound_at: '2026-01-01T00:00:00Z',
	};
}

let agentReads: string[] = [];

function serve(
	creds: Credential[],
	agentsById: Record<string, string[] | 'error'>,
	/** Credentials whose first agents page reports `has_more` (more than one page bound). */
	hasMore: string[] = [],
) {
	worker.use(
		http.get('/credentials', () =>
			HttpResponse.json({ data: creds, has_more: false, next_cursor: null }),
		),
		http.get('/credentials/:id/agents', ({ params }) => {
			const id = String(params.id);
			agentReads.push(id);
			const agents = agentsById[id] ?? [];
			if (agents === 'error') return HttpResponse.json({ detail: 'boom' }, { status: 400 });
			return HttpResponse.json({
				data: agents.map(agent),
				has_more: hasMore.includes(id),
				next_cursor: hasMore.includes(id) ? `${id}-page-2` : null,
			});
		}),
	);
}

/** Renders the agents-with-access figure (count + floor flag) for one API. */
function FigureProbe({ apiRef }: { apiRef: typeof STRIPE }) {
	const index = useApiAccessIndex();
	const credentials = index.credentialsComplete
		? (index.entryFor(apiRef)?.credentials ?? [])
		: null;
	const figureFor = useAgentFigures([credentials], false);
	const figure = figureFor(credentials);
	return <p data-testid="figure">{JSON.stringify(figure)}</p>;
}

/** Renders what the index + agent reads say about `refs`, reading agents for `agentsFor` only. */
function Probe({
	refs,
	agentsFor,
}: {
	refs: Array<typeof STRIPE>;
	agentsFor: Array<typeof STRIPE>;
}) {
	const index = useApiAccessIndex();
	const accessFor = useAgentAccess(agentsFor.map((r) => index.entryFor(r)?.credentials ?? []));
	if (!index.credentialsComplete) return <p>loading</p>;
	return (
		<ul>
			{refs.map((ref) => {
				const entry = index.entryFor(ref);
				const access = accessFor(entry?.credentials ?? []);
				return (
					<li key={ref.vendor} data-testid={`row-${ref.vendor}`}>
						{JSON.stringify({
							credentials: entry?.credentials.map((c) => c.credential_id) ?? [],
							agents: access?.agents.map((a) => a.agent_id) ?? [],
							settled: access?.agentsSettled,
							error: access?.agentsError,
							truncated: access?.agentsTruncated,
							whole: access ? agentsExhaustive(access) : null,
						})}
					</li>
				);
			})}
		</ul>
	);
}

async function rowOf(vendor: string) {
	const el = await screen.findByTestId(`row-${vendor}`);
	return JSON.parse(el.textContent ?? '{}') as {
		credentials: string[];
		agents: string[];
		settled: boolean;
		error: boolean;
		truncated: boolean;
		whole: boolean | null;
	};
}

describe('apiAccess', () => {
	beforeEach(() => {
		setToken('test-token');
		agentReads = [];
	});

	it('counts only ACTIVE credentials whose scope covers the API (wildcards included)', async () => {
		serve(
			[
				makeMockCredential({ credential_id: 'pinned', api: STRIPE }),
				makeMockCredential({
					credential_id: 'any-version',
					api: { vendor: 'stripe', name: 'stripe-api', version: '' },
				}),
				makeMockCredential({ credential_id: 'inactive', api: STRIPE, active: false }),
				makeMockCredential({ credential_id: 'other-api', api: SLACK }),
			],
			{},
		);
		renderWithProviders(<Probe refs={[STRIPE]} agentsFor={[]} />);
		expect((await rowOf('stripe')).credentials).toEqual(['pinned', 'any-version']);
	});

	it('dedupes an agent bound to two covering credentials', async () => {
		serve(
			[
				makeMockCredential({ credential_id: 'a', api: STRIPE }),
				makeMockCredential({ credential_id: 'b', api: STRIPE }),
			],
			{ a: ['agent_1', 'agent_2'], b: ['agent_1'] },
		);
		renderWithProviders(<Probe refs={[STRIPE]} agentsFor={[STRIPE]} />);
		await waitFor(async () => expect((await rowOf('stripe')).settled).toBe(true));
		const row = await rowOf('stripe');
		expect(row.agents).toEqual(['agent_1', 'agent_2']);
		expect(row.whole).toBe(true);
	});

	it('settles a failed read as an error (never an endless loading state)', async () => {
		serve(
			[
				makeMockCredential({ credential_id: 'ok', api: STRIPE }),
				makeMockCredential({ credential_id: 'broken', api: STRIPE }),
			],
			{ ok: ['agent_1'], broken: 'error' },
		);
		renderWithProviders(<Probe refs={[STRIPE]} agentsFor={[STRIPE]} />);
		await waitFor(async () => expect((await rowOf('stripe')).settled).toBe(true));
		const row = await rowOf('stripe');
		expect(row.error).toBe(true);
		expect(row.agents).toEqual(['agent_1']);
		expect(row.whole).toBe(false);
	});

	it('reads agents only for the credentials of the rows asked about', async () => {
		serve(
			[
				makeMockCredential({ credential_id: 'stripe_cred', api: STRIPE }),
				makeMockCredential({ credential_id: 'slack_cred', api: SLACK }),
			],
			{ stripe_cred: ['agent_1'], slack_cred: ['agent_2'] },
		);
		renderWithProviders(<Probe refs={[STRIPE, SLACK]} agentsFor={[STRIPE]} />);
		await waitFor(async () => expect((await rowOf('stripe')).agents).toEqual(['agent_1']));
		expect(agentReads).toEqual(['stripe_cred']);
		// The unasked row's credentials were never read, so its list can't claim to be whole.
		expect((await rowOf('slack')).truncated).toBe(true);
	});

	it('caps the fan-out and reports the capped credentials as truncated', async () => {
		const creds = Array.from({ length: 101 }, (_, i) =>
			makeMockCredential({ credential_id: `c${String(i).padStart(3, '0')}`, api: STRIPE }),
		);
		serve(creds, {});
		renderWithProviders(<Probe refs={[STRIPE]} agentsFor={[STRIPE]} />);
		await waitFor(async () => expect((await rowOf('stripe')).settled).toBe(true), {
			timeout: 5000,
		});
		const row = await rowOf('stripe');
		expect(new Set(agentReads).size).toBe(100);
		expect(row.truncated).toBe(true);
		expect(row.whole).toBe(false);
	});

	it('a first page reporting has_more is truncated, not the whole list', async () => {
		serve(
			[
				makeMockCredential({ credential_id: 'big', api: STRIPE }),
				makeMockCredential({ credential_id: 'small', api: STRIPE }),
			],
			{ big: ['agent_1', 'agent_2'], small: ['agent_3'] },
			['big'],
		);
		renderWithProviders(<Probe refs={[STRIPE]} agentsFor={[STRIPE]} />);
		await waitFor(async () => expect((await rowOf('stripe')).settled).toBe(true));
		const row = await rowOf('stripe');
		expect(row.agents).toEqual(['agent_1', 'agent_2', 'agent_3']);
		expect(row.error).toBe(false);
		expect(row.truncated).toBe(true);
		expect(row.whole).toBe(false);
	});

	it('figures: a truncated read counts as a floor ("N+"), an exhaustive one as exact', async () => {
		serve(
			[makeMockCredential({ credential_id: 'big', api: STRIPE })],
			{
				big: ['agent_1', 'agent_2'],
			},
			['big'],
		);
		const { unmount } = renderWithProviders(<FigureProbe apiRef={STRIPE} />);
		await waitFor(() =>
			expect(JSON.parse(screen.getByTestId('figure').textContent ?? '{}')).toEqual({
				agentCount: 2,
				agentsAtLeast: true,
				agentsLoading: false,
			}),
		);
		unmount();

		serve([makeMockCredential({ credential_id: 'whole', api: STRIPE })], {
			whole: ['agent_1'],
		});
		renderWithProviders(<FigureProbe apiRef={STRIPE} />);
		await waitFor(() =>
			expect(JSON.parse(screen.getByTestId('figure').textContent ?? '{}')).toEqual({
				agentCount: 1,
				agentsAtLeast: false,
				agentsLoading: false,
			}),
		);
	});

	it('figures: a failed read stays unknown, never a floor', async () => {
		serve(
			[
				makeMockCredential({ credential_id: 'big', api: STRIPE }),
				makeMockCredential({ credential_id: 'broken', api: STRIPE }),
			],
			{ big: ['agent_1'], broken: 'error' },
			['big'],
		);
		renderWithProviders(<FigureProbe apiRef={STRIPE} />);
		await waitFor(() =>
			expect(JSON.parse(screen.getByTestId('figure').textContent ?? '{}')).toEqual({
				agentCount: null,
				agentsAtLeast: false,
				agentsLoading: false,
			}),
		);
	});
});
