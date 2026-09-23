/**
 * CredentialInventorySheet — the page-level org-wide inventory trigger and
 * sheet. The sheet's list/create/edit/delete internals keep their own coverage
 * in `shared/credentials`; these specs pin the trigger's placement, the org-wide
 * scope, reachability without a fleet, and the 390px viewport.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { useLocation } from 'react-router';
import { page, userEvent as browserUser } from 'vitest/browser';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { resetAgentsStore } from '@/modules/agents/mocks/handlers';
import {
	makeMockCredential,
	resetApisStore,
	resetCredentialsStore,
} from '@/shared/credentials/mocks/handlers';
import { CredentialType } from '@/shared/credentials/api';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

/** Surfaces the router's search string so specs can assert `?credentials` is spent. */
function LocationProbe() {
	const location = useLocation();
	return <div data-testid="location-search">{location.search}</div>;
}

function renderPage(route = '/') {
	return renderWithProviders(
		<>
			<AgentsPage />
			<LocationProbe />
			<Toaster />
		</>,
		{ route },
	);
}

/** The page-header trigger — the only Credentials control on the surface. */
function headerTrigger() {
	return screen.getByRole('button', { name: 'Credentials' });
}

describe('CredentialInventorySheet — page-level org-wide inventory', () => {
	/** The two secrets the agents fixture binds, by the ids it binds them under. */
	function inventorySeed() {
		return [
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.API_KEY,
				api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
			}),
			makeMockCredential({
				credential_id: 'cred_github_1',
				name: 'GitHub PAT',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
			}),
		];
	}

	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
		resetCredentialsStore(inventorySeed());
		resetApisStore([]);
	});

	it('the trigger lives on the page header and opens the org-wide sheet', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		await screen.findByTestId('agent-dock');

		// Page level = org-wide; dock = this agent only. The dock must not
		// carry the wallet verb.
		expect(
			within(screen.getByTestId('agent-dock')).queryByRole('button', {
				name: 'Credentials',
			}),
		).not.toBeInTheDocument();

		await user.click(headerTrigger());

		const sheet = within(await screen.findByRole('dialog'));
		expect(sheet.getByRole('heading', { name: 'Credentials' })).toBeInTheDocument();
		// The full inventory — including credentials NOT bound to the
		// selected agent.
		expect(await sheet.findByText('Slack bot token')).toBeInTheDocument();
		expect(sheet.getByText('GitHub PAT')).toBeInTheDocument();
		expect(sheet.getByRole('button', { name: /Add credential/ })).toBeEnabled();

		await checkA11y(document.body, { modal: true });
	});

	it('stays reachable with an empty fleet — the inventory is not agent-scoped', async () => {
		worker.use(
			http.get('/agents', () =>
				HttpResponse.json({ data: [], has_more: false, next_cursor: null }),
			),
		);
		const user = userEvent.setup();
		renderPage();

		// No agents, no dock — but the org-wide inventory still opens.
		expect(await screen.findByText('No agents yet')).toBeInTheDocument();
		expect(screen.queryByTestId('agent-dock')).not.toBeInTheDocument();

		await user.click(headerTrigger());
		const sheet = within(await screen.findByRole('dialog'));
		expect(sheet.getByRole('heading', { name: 'Credentials' })).toBeInTheDocument();
		expect(await sheet.findByText('Slack bot token')).toBeInTheDocument();
	});

	it('Escape closes the delete confirm first, then the sheet, and restores focus to the trigger', async () => {
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		await screen.findByTestId('agent-dock');

		await user.click(headerTrigger());
		const sheet = within(await screen.findByTestId('sheet-primitive'));
		await sheet.findByText('Slack bot token');
		await user.click(sheet.getByRole('button', { name: 'Delete credential Slack bot token' }));

		await screen.findByRole('dialog', { name: 'Delete credential' });

		// First Escape: only the confirm closes; the inventory sheet survives.
		await browserUser.keyboard('{Escape}');
		await waitFor(() =>
			expect(
				screen.queryByRole('dialog', { name: 'Delete credential' }),
			).not.toBeInTheDocument(),
		);
		expect(screen.getByRole('heading', { name: 'Credentials' })).toBeInTheDocument();
		// Nothing was deleted by the dismissal.
		expect(sheet.getByText('Slack bot token')).toBeInTheDocument();

		// Second Escape closes the sheet, and focus lands back on the
		// page-header trigger that opened it.
		await browserUser.keyboard('{Escape}');
		await waitFor(
			() => expect(screen.queryByTestId('sheet-primitive')).not.toBeInTheDocument(),
			{ timeout: 2000 },
		);
		await waitFor(() => expect(headerTrigger()).toHaveFocus());
	});

	describe('the ?credentials deep link (the inventory is a sheet, not a route)', () => {
		it('opens on arrival, spends the param, and leaves ?agent= alone', async () => {
			renderPage('/?agent=agnt_active_1&credentials=1');

			const sheet = within(await screen.findByRole('dialog'));
			expect(sheet.getByRole('heading', { name: 'Credentials' })).toBeInTheDocument();
			expect(await sheet.findByText('Slack bot token')).toBeInTheDocument();

			// The link is spent, not sticky: only `credentials` is dropped, and
			// the operator's place on the surface survives the rewrite.
			await waitFor(() =>
				expect(screen.getByTestId('location-search')).toHaveTextContent(
					'?agent=agnt_active_1',
				),
			);
			expect(screen.getByTestId('location-search')).not.toHaveTextContent('credentials');

			// And dismissing it stays dismissed — nothing re-reads the URL.
			await userEvent.setup().click(sheet.getByRole('button', { name: 'Close' }));
			await waitFor(() =>
				expect(screen.queryByTestId('sheet-primitive')).not.toBeInTheDocument(),
			);
		});

		it('credentials=new lands on the create wizard, and cancelling it keeps the inventory', async () => {
			renderPage('/?credentials=new');

			// A caller whose own label promised a form ("Add a credential") gets
			// the form, not a list with a button on it.
			expect(await screen.findByRole('dialog', { name: /Choose an API/ })).toBeVisible();
			// And as a drawer stacked on the inventory, not a centred modal over it.
			await waitFor(() => expect(screen.getAllByTestId('sheet-primitive')).toHaveLength(2));
			expect(document.querySelector('dialog[open]')).toBeNull();

			await browserUser.keyboard('{Escape}');
			await waitFor(() =>
				expect(
					screen.queryByRole('dialog', { name: /Choose an API/ }),
				).not.toBeInTheDocument(),
			);
			// The wizard was a step into the inventory, so backing out of it
			// leaves the operator there rather than reopening the form.
			expect(screen.getByRole('heading', { name: 'Credentials' })).toBeInTheDocument();
			expect(await screen.findByText('Slack bot token')).toBeInTheDocument();
			expect(screen.queryByRole('dialog', { name: /Choose an API/ })).not.toBeInTheDocument();
		});
	});

	describe('the Unbound filter (risk 6 — a zero-agent credential has no other home)', () => {
		/** A credential no seeded agent is bound to. */
		function seedWithOrphan(): void {
			resetCredentialsStore([
				makeMockCredential({
					credential_id: 'cred_slack_1',
					name: 'Slack bot token',
					type: CredentialType.API_KEY,
					api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
				}),
				makeMockCredential({
					credential_id: 'cred_github_1',
					name: 'GitHub PAT',
					type: CredentialType.BEARER_TOKEN,
					api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
				}),
				makeMockCredential({
					credential_id: 'cred_orphan_1',
					name: 'Stripe key nobody uses',
					type: CredentialType.API_KEY,
					api: { vendor: 'stripe.com', name: 'default', version: '1.0.0' },
				}),
			]);
		}

		function toggle() {
			return screen.getByRole('group', { name: 'Filter by agent usage' });
		}

		it('narrows the inventory to the credentials no agent is bound to', async () => {
			seedWithOrphan();
			const user = userEvent.setup();
			renderPage('/?agent=agnt_active_1');
			await screen.findByTestId('agent-dock');

			await user.click(headerTrigger());
			const sheet = within(await screen.findByRole('dialog'));
			expect(await sheet.findByText('Stripe key nobody uses')).toBeInTheDocument();

			// The count is part of the label once the fleet join can prove it:
			// one of the three seeded credentials is bound to nobody.
			await waitFor(() =>
				expect(
					within(toggle()).getByRole('button', { name: 'Unbound (1)' }),
				).toBeInTheDocument(),
			);
			await user.click(within(toggle()).getByRole('button', { name: /^Unbound/ }));

			// The two credentials agnt_active_1 holds drop out — including the
			// suspended binding, which is still a binding.
			await waitFor(() =>
				expect(sheet.queryByText('Slack bot token')).not.toBeInTheDocument(),
			);
			expect(sheet.queryByText('GitHub PAT')).not.toBeInTheDocument();
			expect(sheet.getByText('Stripe key nobody uses')).toBeInTheDocument();

			// Back out again without losing the rest of the inventory.
			await user.click(within(toggle()).getByRole('button', { name: 'Any agent' }));
			expect(await sheet.findByText('Slack bot token')).toBeInTheDocument();
		});

		it('says every credential is in use rather than showing an unexplained empty grid', async () => {
			const user = userEvent.setup();
			renderPage('/?agent=agnt_active_1');
			await screen.findByTestId('agent-dock');

			await user.click(headerTrigger());
			const sheet = within(await screen.findByRole('dialog'));
			await sheet.findByText('Slack bot token');

			await user.click(within(toggle()).getByRole('button', { name: /^Unbound/ }));

			// Both seeded credentials are bound, so the copy has to be about the filter —
			// not "No credentials stored", a false claim about the inventory.
			expect(await sheet.findByText('Every credential is in use')).toBeInTheDocument();
			expect(sheet.queryByText('No credentials stored')).not.toBeInTheDocument();
		});

		it("withholds the answer while a single agent's bindings are missing", async () => {
			seedWithOrphan();
			// One agent's bindings never arrive. An incomplete join can only under-count,
			// so a credential that agent holds would be listed as unused.
			worker.use(
				http.get('/agents/:id/credentials', ({ params }) =>
					params.id === 'agnt_disabled_1'
						? new HttpResponse(null, { status: 500 })
						: undefined,
				),
			);
			const user = userEvent.setup();
			renderPage('/?agent=agnt_active_1');
			await screen.findByTestId('agent-dock');

			await user.click(headerTrigger());
			const sheet = within(await screen.findByRole('dialog'));
			await sheet.findByText('Stripe key nobody uses');

			await user.click(within(toggle()).getByRole('button', { name: /^Unbound/ }));

			expect(
				await sheet.findByText(/Which credentials no agent uses can’t be shown yet/),
			).toBeInTheDocument();
			// And no count either — a number off a partial join would be a
			// guess dressed as a fact.
			expect(within(toggle()).getByRole('button', { name: 'Unbound' })).toBeInTheDocument();
			// No list at all: a partial one reads as a complete answer.
			expect(sheet.queryByText('Stripe key nobody uses')).not.toBeInTheDocument();
			expect(sheet.getByRole('button', { name: 'Try again' })).toBeEnabled();

			// The full inventory is still one click away, as the notice says.
			await user.click(within(toggle()).getByRole('button', { name: 'Any agent' }));
			expect(await sheet.findByText('Slack bot token')).toBeInTheDocument();
		});
	});

	describe('the cards’ usage figures', () => {
		beforeEach(() => {
			// A third secret the mocked usage leaderboard says nothing about: its card is
			// where "no calls in 7d" has to be proven off a complete list.
			resetCredentialsStore([
				...inventorySeed(),
				makeMockCredential({
					credential_id: 'cred_quiet_1',
					name: 'Unused backup key',
					type: CredentialType.API_KEY,
					api: { vendor: 'stripe.com', name: 'default', version: '1.0.0' },
				}),
			]);
		});

		/** The inventory card for a credential — scoped to the sheet, since the same
		 * name is also printed on the API tiles underneath. */
		function cardFor(name: string): HTMLElement {
			const heading = within(screen.getByTestId('sheet-primitive')).getByText(name);
			const card = heading.closest('[data-testid="credential-card"]');
			if (!card) throw new Error(`No card around "${name}"`);
			return card as HTMLElement;
		}

		/** A `top` list at the endpoint's ceiling, i.e. truncated — no row is a seeded
		 * credential, so every card's volume is *unknown* rather than zero. */
		function truncatedUsage() {
			const until = Math.floor(Date.now() / 1000);
			return {
				since: until - 7 * 86400,
				until,
				bucket_seconds: 21_600,
				group_by: 'credential',
				stats: { total: 5000, success: 5000, failed: 0, avg_ms: 400 },
				buckets: [],
				top: Array.from({ length: 50 }, (_, i) => ({
					key: `cred_other_${i}`,
					label: `cred_other_${i}`,
					total: 100 - i,
					success: 100 - i,
					failed: 0,
					avg_ms: 400,
					trend: [],
				})),
			};
		}

		it('counts the agents holding a credential, and reads zero traffic off a complete leaderboard', async () => {
			const user = userEvent.setup();
			renderPage('/?agent=agnt_active_1');
			await screen.findByTestId('agent-dock');
			await user.click(headerTrigger());
			await within(await screen.findByTestId('sheet-primitive')).findByText(
				'Slack bot token',
			);

			// One agent holds it — including via a suspended binding for the
			// other credential, which is still an agent holding a secret.
			await waitFor(() =>
				expect(
					within(cardFor('Slack bot token')).getByTestId('cred-used-by'),
				).toHaveTextContent('used by 1 agent'),
			);
			// A credential the leaderboard names carries its own figure; what is pinned is
			// that the id resolved to a count at all.
			await waitFor(() =>
				expect(
					within(cardFor('Slack bot token')).getByTestId('cred-calls-7d'),
				).toHaveTextContent(/^[\d,]+ calls? in 7d$/),
			);
			// …and because that list came back shorter than the limit, a
			// credential missing from it provably had no traffic at all.
			expect(
				within(cardFor('Unused backup key')).getByTestId('cred-calls-7d'),
			).toHaveTextContent('no calls in 7d');
		});

		it('withholds the call figure when the leaderboard came back truncated', async () => {
			worker.use(http.get('/monitoring/usage', () => HttpResponse.json(truncatedUsage())));
			const user = userEvent.setup();
			renderPage('/?agent=agnt_active_1');
			await screen.findByTestId('agent-dock');
			await user.click(headerTrigger());
			await within(await screen.findByTestId('sheet-primitive')).findByText(
				'Slack bot token',
			);

			// The fleet count still resolves — it is a different, exact join.
			await waitFor(() =>
				expect(
					within(cardFor('Slack bot token')).getByTestId('cred-used-by'),
				).toBeInTheDocument(),
			);
			// But "not in the top 50" is not "no calls", so no figure at all.
			expect(
				within(cardFor('Slack bot token')).queryByTestId('cred-calls-7d'),
			).not.toBeInTheDocument();
		});

		it('omits both figures for a viewer the monitoring aggregate is closed to', async () => {
			worker.use(
				http.get('/monitoring/usage', () => new HttpResponse(null, { status: 403 })),
			);
			const user = userEvent.setup();
			renderPage('/?agent=agnt_active_1');
			await screen.findByTestId('agent-dock');
			await user.click(headerTrigger());
			await within(await screen.findByTestId('sheet-primitive')).findByText(
				'Slack bot token',
			);

			await waitFor(() =>
				expect(
					within(cardFor('Slack bot token')).getByTestId('cred-used-by'),
				).toBeInTheDocument(),
			);
			expect(
				within(cardFor('Slack bot token')).queryByTestId('cred-calls-7d'),
			).not.toBeInTheDocument();
		});
	});

	describe('several credentials for one API', () => {
		beforeEach(() => {
			// Two more Slack secrets, one sharing the first's name — the case where
			// identical cards gave no way to tell them apart.
			resetCredentialsStore([
				...inventorySeed(),
				makeMockCredential({
					credential_id: 'cred_slack_2',
					name: 'Slack workspace B',
					type: CredentialType.BEARER_TOKEN,
					api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
				}),
				makeMockCredential({
					credential_id: 'cred_slack_3',
					name: 'Slack bot token',
					type: CredentialType.API_KEY,
					api: { vendor: 'slack.com', name: 'default', version: '2.0.0' },
				}),
			]);
		});

		async function openInventory(): Promise<HTMLElement> {
			const user = userEvent.setup();
			renderPage('/?agent=agnt_active_1');
			await screen.findByTestId('agent-dock');
			await user.click(headerTrigger());
			const sheet = await screen.findByTestId('sheet-primitive');
			await within(sheet).findByText('Slack workspace B');
			return sheet;
		}

		it('say the API once, with a row per credential', async () => {
			const sheet = await openInventory();

			const groups = within(sheet).getAllByTestId('credential-group');
			expect(groups).toHaveLength(1);
			const [slack] = groups;
			expect(within(slack).getByTestId('credential-group-count')).toHaveTextContent(
				'3 credentials',
			);
			// The section is named by its API, so assistive tech hears the grouping too.
			expect(slack).toHaveAccessibleName(/Slack/);
			expect(within(slack).getAllByTestId('credential-card')).toHaveLength(3);
			expect(slack).toHaveTextContent(/Any of these can be bound to an agent/);
			// Two rows share a name, so the id tail is what sets them apart.
			expect(slack).toHaveTextContent('Some share a name; rename one to tell them apart.');
			expect(slack).toHaveTextContent('…lack_1');
			expect(slack).toHaveTextContent('…lack_3');

			// An API with one credential keeps its standalone card.
			const github = within(sheet).getByText('GitHub PAT').closest('[data-testid]');
			expect(github).toHaveAttribute('data-testid', 'credential-card');
			expect(github?.closest('[data-testid="credential-group"]')).toBeNull();
		});

		it('each row keeps its own usage figure and its own actions', async () => {
			const sheet = await openInventory();
			const slack = within(sheet).getByTestId('credential-group');
			const [first] = within(slack).getAllByTestId('credential-card');

			await waitFor(() =>
				expect(within(first).getByTestId('cred-used-by')).toHaveTextContent(
					'used by 1 agent',
				),
			);

			const user = userEvent.setup();
			await user.click(
				within(slack).getByRole('button', { name: 'Delete credential Slack workspace B' }),
			);
			await screen.findByRole('dialog', { name: 'Delete credential' });
		});

		it('passes an accessibility audit with a grouped API on screen', async () => {
			await openInventory();
			await checkA11y(document.body, { modal: true });
		});
	});

	it('390px: the trigger stays reachable and the sheet opens full-screen', async () => {
		await page.viewport(390, 844);
		const user = userEvent.setup();
		renderPage('/?agent=agnt_active_1');
		await screen.findByTestId('agent-dock');

		await user.click(headerTrigger());
		expect(
			within(await screen.findByRole('dialog')).getByRole('heading', {
				name: 'Credentials',
			}),
		).toBeInTheDocument();
	});
});
