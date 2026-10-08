import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page } from 'vitest/browser';
import {
	renderWithProviders,
	screen,
	waitFor,
	within,
	userEvent,
	checkA11y,
	createErrorHandler,
} from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { Toaster } from '@/shared/ui';
import { resetAgentsStore, seedCredentialBindings } from '@/modules/agents/mocks/handlers';
import { agentsKeysForTest } from '@/modules/agents/api/hooks';
import {
	makeMockCredential,
	resetApisStore,
	resetCredentialsStore,
} from '@/shared/credentials/mocks/handlers';
import { CredentialType, type ApiResponse } from '@/shared/credentials/api';
import { AuthProvider } from '@/shared/auth';
import AgentsPage from '@/modules/agents/pages/AgentsPage';

/**
 * The API access sidebar — one tile's access in one panel: the credential, the
 * rules editor, the dry-run tester (gated by an unsaved draft), the connect flow,
 * and the two DISTINCT destructive verbs (unbind this agent vs delete the
 * credential org-wide). Rendered through the full AgentsPage, so the wiring is real.
 */

/** Workspace API row for the registry mock. */
function apiRow(vendor: string, displayName: string, operationCount: number): ApiResponse {
	return {
		_links: { self: `/apis/${vendor}`, openapi: `/apis/${vendor}/openapi` },
		api: { vendor, name: 'default', version: '1.0.0' },
		catalog_api_id: null,
		created_at: '2026-01-01T00:00:00Z',
		current_revision_id: null,
		description: null,
		display_name: displayName,
		icon_url: null,
		operation_count: operationCount,
		revision_count: 1,
		security_schemes: [],
		updated_at: '2026-01-01T00:00:00Z',
	} as unknown as ApiResponse;
}

/** The org credentials + workspace APIs behind `agnt_active_1`'s bindings. */
function seedComposedStores() {
	resetCredentialsStore([
		makeMockCredential({
			credential_id: 'cred_slack_1',
			name: 'Slack bot token',
			type: CredentialType.BEARER_TOKEN,
			api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
		}),
		makeMockCredential({
			credential_id: 'cred_github_1',
			name: 'GitHub PAT',
			type: CredentialType.BEARER_TOKEN,
			api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
		}),
	]);
	resetApisStore([
		{ row: apiRow('slack.com', 'Slack', 181), spec: {} },
		// Vendor `github` (not `github.com`) is what the shared binding fixture
		// serves, so this row is the one the GitHub tile resolves against.
		{ row: apiRow('github', 'GitHub', 912), spec: {} },
	]);
}

function renderPage(route = '/?agent=agnt_active_1') {
	return renderWithProviders(
		<>
			<AgentsPage />
			<Toaster />
		</>,
		{ route },
	);
}

/** The stretched overlay button that makes the whole tile clickable. */
function tileOpener(title: string): HTMLElement {
	return screen.getByRole('button', { name: `${title} — open access details` });
}

/** The sidebar dialog, named by the clicked tile's API title. */
async function openSidebar(title: string): Promise<HTMLElement> {
	const user = userEvent.setup();
	await user.click(tileOpener(title));
	const dialog = await screen.findByRole('dialog', { name: title });
	// Wait until the sheet has finished ENTERING: SheetPrimitive auto-focuses its
	// first focusable child then, which is also when its Escape handler arms.
	await waitFor(() => {
		expect(within(dialog).getAllByRole('button')[0]).toHaveFocus();
	});
	return dialog;
}

describe('ApiAccessSidebar — the API tile access panel', () => {
	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
		seedComposedStores();
	});

	/**
	 * The rules editor lists rules read-only; a change goes through one rule's
	 * inline form. Opens the first rule, appends `suffix` to its path and commits
	 * the rule — which leaves the BINDING's draft dirty until `Save rules`.
	 */
	async function editFirstRulePath(
		user: ReturnType<typeof userEvent.setup>,
		inDialog: ReturnType<typeof within>,
		suffix: string,
	): Promise<void> {
		await user.click(inDialog.getAllByRole('button', { name: 'Edit rule' })[0]);
		await user.type(inDialog.getByLabelText('Path pattern'), suffix);
		await user.click(inDialog.getByRole('button', { name: 'Save' }));
	}

	// --- Opening: content, keyboard, focus restore --------------------------

	it('tile click opens the sidebar with that binding’s credential, rules and tester', async () => {
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);

		// The tile's aria wiring reflects the open panel.
		expect(tileOpener('Slack')).toHaveAttribute('aria-expanded', 'true');
		expect(tileOpener('GitHub')).toHaveAttribute('aria-expanded', 'false');

		// 1 — the credential (read view + the edit affordance).
		expect(inDialog.getByText('Slack bot token')).toBeInTheDocument();
		expect(inDialog.getByRole('button', { name: /Edit credential/ })).toBeInTheDocument();
		// The full id, copyable, for a call that names it. One credential for the
		// API needs no header, so no header snippet.
		expect(inDialog.getByTestId('credential-id-row')).toHaveTextContent('IDcred_slack_1');
		expect(
			inDialog.getByRole('button', { name: 'Copy the credential ID' }),
		).toBeInTheDocument();
		expect(inDialog.queryByText(/^Jentic-Credential-Id:/)).not.toBeInTheDocument();

		// 2 — the rules editor, keyed to THIS binding: the seeded rule's path.
		expect(
			await inDialog.findByText('Permission rules for Slack bot token'),
		).toBeInTheDocument();
		expect(inDialog.getByText('/chat. (prefix)')).toBeInTheDocument();

		// 3 — the tester, live (no unsaved draft yet) with honest saved-rules copy.
		expect(inDialog.getByText('Test a request')).toBeInTheDocument();
		expect(inDialog.getByLabelText('Request path')).toBeEnabled();
		expect(inDialog.getByText(/dry-runs the broker's decision/i)).toBeInTheDocument();
		// The optional operation id is a disclosure, closed until asked for.
		expect(inDialog.queryByLabelText('Operation ID (optional)')).not.toBeInTheDocument();

		// Single-API credential → no blast-radius note.
		expect(inDialog.queryByTestId('blast-radius-note')).not.toBeInTheDocument();

		// The two destructive verbs are both present and distinctly worded.
		expect(
			inDialog.getByRole('button', { name: 'Unbind Slack bot token from support-agent' }),
		).toBeInTheDocument();
		expect(
			inDialog.getByRole('button', { name: 'Delete credential Slack bot token org-wide' }),
		).toBeInTheDocument();
	});

	it('keeps "Allow all operations" in the new rules editor, as a pending catch-all', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const inDialog = within(await openSidebar('Slack'));
		await inDialog.findByText('Permission rules for Slack bot token');

		// The one-click broad grant survives the editor swap: it appends the
		// constrained catch-all (never a condition-less allow the API rejects)
		// and shows up as a pending change until it is saved.
		await user.click(inDialog.getByRole('button', { name: /Allow all operations/ }));
		expect(await inDialog.findByTestId('rules-diff')).toHaveTextContent(/Added/);
		expect(inDialog.getByText('.*')).toBeInTheDocument();
		// Already granting everything, the shortcut stands down.
		expect(
			inDialog.queryByRole('button', { name: /Allow all operations/ }),
		).not.toBeInTheDocument();
	});

	it('Allow all → Edit → Save keeps the catch-all a REGEX (a prefix ".*" would grant nothing)', async () => {
		const user = userEvent.setup();
		const bodies: unknown[] = [];
		worker.events.on('request:start', ({ request }) => {
			if (request.method === 'PUT' && request.url.includes('/permissions')) {
				void request
					.clone()
					.json()
					.then((b: unknown) => bodies.push(b));
			}
		});
		renderPage();
		await screen.findByText('1 access rule');
		const inDialog = within(await openSidebar('Slack'));
		await inDialog.findByText('Permission rules for Slack bot token');

		await user.click(inDialog.getByRole('button', { name: /Allow all operations/ }));
		// Edit the new catch-all (the second row) — narrow it to GET — and save.
		await user.click(inDialog.getAllByRole('button', { name: 'Edit rule' })[1]);
		expect(inDialog.getByLabelText('Path match mode')).toHaveValue('regex');
		await user.click(inDialog.getByRole('button', { name: 'GET' }));
		await user.click(inDialog.getByRole('button', { name: 'Save' }));
		await user.click(inDialog.getByRole('button', { name: /Save rules/ }));

		await waitFor(() => expect(bodies).toHaveLength(1));
		worker.events.removeAllListeners('request:start');
		const saved = bodies[0] as { path?: string; match_mode?: string; methods?: string[] }[];
		const catchAll = saved.find((r) => r.path === '.*');
		expect(catchAll?.methods).toEqual(['GET']);
		// `regex` is the server default, so it may be omitted — but never prefix/exact.
		expect(catchAll?.match_mode ?? 'regex').toBe('regex');
	});

	it('stays open after Save with a "Saved" confirmation, retired by the next edit', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');
		const inDialog = within(await openSidebar('Slack'));
		await inDialog.findByText('Permission rules for Slack bot token');

		await editFirstRulePath(user, inDialog, 'post');
		await user.click(inDialog.getByRole('button', { name: /Save rules/ }));
		expect(await inDialog.findByTestId('rules-saved')).toHaveTextContent('Saved');
		expect(inDialog.getByText('Permission rules for Slack bot token')).toBeInTheDocument();

		await editFirstRulePath(user, inDialog, 'x');
		expect(inDialog.queryByTestId('rules-saved')).not.toBeInTheDocument();
		expect(inDialog.getByTestId('rules-dirty-hint')).toHaveTextContent('Unsaved changes');
	});

	it('fades the body only while content continues below it', async () => {
		// A short viewport forces the panel to overflow; the danger zone is its
		// last section, so the fade is what says it exists at all.
		await page.viewport(1280, 460);
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);
		await waitFor(() => {
			expect(inDialog.getByTestId('sidebar-scroll-fade')).toBeInTheDocument();
		});

		const scroller = inDialog.getByTestId('sidebar-danger-zone').closest('div.overflow-y-auto');
		scroller?.scrollTo(0, scroller.scrollHeight);
		// At the end of the scroll the fade must go — it would otherwise promise
		// content that isn't there.
		await waitFor(() => {
			expect(inDialog.queryByTestId('sidebar-scroll-fade')).not.toBeInTheDocument();
		});
	});

	it('discloses the optional operation id, and clears it when closed again', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const inDialog = within(await openSidebar('Slack'));
		await user.click(inDialog.getByRole('button', { name: /operation id/ }));

		const field = inDialog.getByLabelText('Operation ID (optional)');
		await user.type(field, 'chat.postMessage');
		await user.click(inDialog.getByRole('button', { name: 'Remove operation id' }));
		expect(inDialog.queryByLabelText('Operation ID (optional)')).not.toBeInTheDocument();

		// Re-opening must not restore a value the operator dismissed — a hidden
		// operation id would silently change the next verdict.
		await user.click(inDialog.getByRole('button', { name: /operation id/ }));
		expect(inDialog.getByLabelText('Operation ID (optional)')).toHaveValue('');
	});

	it('the rehosted tester dry-runs the SAVED rules of the clicked binding', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);

		// The seeded rule allows POST /chat.* — a POST to /chat.postMessage
		// must come back allowed and NAME the matching rule.
		await user.selectOptions(inDialog.getByLabelText('HTTP method'), 'POST');
		await user.type(inDialog.getByLabelText('Request path'), '/chat.postMessage');
		await user.click(inDialog.getByRole('button', { name: 'Test' }));

		const verdict = await inDialog.findByTestId('rule-verdict');
		expect(verdict).toHaveTextContent('Allowed');
		expect(verdict).toHaveTextContent('matched rule #1');
	});

	it('opens from the keyboard and restores focus to the tile on close', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const opener = tileOpener('Slack');
		opener.focus();
		await user.keyboard('{Enter}');
		const dialog = await screen.findByRole('dialog', { name: 'Slack' });
		// The Escape handler arms when the enter animation settles (the sheet
		// auto-focuses its first focusable child at that moment).
		await waitFor(() => {
			expect(within(dialog).getAllByRole('button')[0]).toHaveFocus();
		});

		await user.keyboard('{Escape}');
		await waitFor(() => {
			expect(screen.queryByRole('dialog', { name: 'Slack' })).not.toBeInTheDocument();
		});
		// Focus returns to the element that opened the sheet.
		await waitFor(() => expect(tileOpener('Slack')).toHaveFocus());
		expect(tileOpener('Slack')).toHaveAttribute('aria-expanded', 'false');
	});

	// --- Blast radius: one credential, several APIs --------------------------

	it('names the OTHER APIs sharing the binding when one credential serves several', async () => {
		// One credential fanning out to two workspace APIs: rules are keyed by
		// (agent, credential), so editing from either tile changes both.
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_multi_1',
				name: 'Shared service token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'notion.so', name: 'default', version: '1.0.0' },
			}),
		]);
		resetApisStore([
			{ row: apiRow('notion.so', 'Notion', 40), spec: {} },
			{ row: apiRow('stripe.com', 'Stripe', 55), spec: {} },
		]);
		seedCredentialBindings([
			{
				agent_id: 'agnt_active_1',
				credential_id: 'cred_multi_1',
				name: 'Shared service token',
				serves: [
					{ api_vendor: 'notion.so', api_name: null, api_version: null },
					{ api_vendor: 'stripe.com', api_name: null, api_version: null },
				],
			},
		]);

		renderPage();
		await screen.findByText('Notion');

		const dialog = await openSidebar('Notion');
		const note = await within(dialog).findByTestId('blast-radius-note');
		expect(note).toHaveTextContent('This credential also serves Stripe');
		expect(note).toHaveTextContent(/changing them changes access to that API too/i);
	});

	// --- Tester vs draft ------------------------------------------------------

	it('disables the tester while the rules editor is dirty and re-enables after save', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);
		await inDialog.findByText('Permission rules for Slack bot token');

		// Dirty the draft: narrow the rule's path prefix.
		await editFirstRulePath(user, inDialog, 'post');

		// The tester pauses and says WHY (it evaluates saved rules only).
		expect(await inDialog.findByTestId('rule-tester-disabled-note')).toHaveTextContent(
			/unsaved changes/,
		);
		expect(inDialog.getByLabelText('Request path')).toBeDisabled();
		expect(inDialog.getByRole('button', { name: 'Test' })).toBeDisabled();

		// Save the draft → the tester comes back.
		await user.click(inDialog.getByRole('button', { name: /Save rules/ }));
		await waitFor(() => {
			expect(inDialog.queryByTestId('rule-tester-disabled-note')).not.toBeInTheDocument();
		});
		expect(inDialog.getByLabelText('Request path')).toBeEnabled();

		// The editor stays mounted after save (sidebar host has no onClose) —
		// and the grid's rule count still reflects the binding.
		expect(inDialog.getByText('Permission rules for Slack bot token')).toBeInTheDocument();
	});

	it('re-enables the tester when the draft is discarded', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);
		await inDialog.findByText('Permission rules for Slack bot token');

		await editFirstRulePath(user, inDialog, 'x');
		expect(await inDialog.findByTestId('rule-tester-disabled-note')).toBeInTheDocument();

		// The sidebar host renders Discard (not Cancel — nothing to dismiss).
		await user.click(inDialog.getByRole('button', { name: /Discard changes/ }));
		await waitFor(() => {
			expect(inDialog.queryByTestId('rule-tester-disabled-note')).not.toBeInTheDocument();
		});
		expect(inDialog.getByText('/chat. (prefix)')).toBeInTheDocument();
	});

	it('a pre-save verdict does not reappear after editing and saving the rules', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);
		await inDialog.findByText('Permission rules for Slack bot token');

		// Dry-run against the SAVED rules (allow POST prefix /chat.) → Allowed.
		await user.selectOptions(inDialog.getByLabelText('HTTP method'), 'POST');
		await user.type(inDialog.getByLabelText('Request path'), '/chat.postMessage');
		await user.click(inDialog.getByRole('button', { name: 'Test' }));
		expect(await inDialog.findByTestId('rule-verdict')).toHaveTextContent('Allowed');

		// Edit the rule so the SAME request would now be denied, then save.
		await editFirstRulePath(user, inDialog, 'x'); // → '/chat.x'
		expect(await inDialog.findByTestId('rule-tester-disabled-note')).toBeInTheDocument();
		await user.click(inDialog.getByRole('button', { name: /Save rules/ }));

		// The tester re-enables — WITHOUT resurrecting the pre-save verdict, which
		// claimed "Allowed" for a request the new rules deny.
		await waitFor(() => {
			expect(inDialog.queryByTestId('rule-tester-disabled-note')).not.toBeInTheDocument();
			expect(inDialog.queryByTestId('rule-verdict')).not.toBeInTheDocument();
		});

		// A fresh dry-run works and reflects the NEW rules.
		await user.click(inDialog.getByRole('button', { name: 'Test' }));
		expect(await inDialog.findByTestId('rule-verdict')).toHaveTextContent(
			'Denied — no rule matched (default deny)',
		);
	});

	it('keeps the verdict across a refetch that returns identical rules', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);
		await user.selectOptions(inDialog.getByLabelText('HTTP method'), 'POST');
		await user.type(inDialog.getByLabelText('Request path'), '/chat.postMessage');
		await user.click(inDialog.getByRole('button', { name: 'Test' }));
		expect(await inDialog.findByTestId('rule-verdict')).toHaveTextContent('Allowed');

		// A background refetch of the SAME rule content (new array identity) must not
		// drop the verdict: the reset compares content, not identity.
		await queryClient.invalidateQueries({
			queryKey: agentsKeysForTest.bindingPermissions('agnt_active_1', 'cred_slack_1'),
		});
		expect(inDialog.getByTestId('rule-verdict')).toHaveTextContent('Allowed');
	});

	it('a rules-load error while the draft is dirty re-enables the tester', async () => {
		const user = userEvent.setup();
		const { queryClient } = renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);
		await inDialog.findByText('Permission rules for Slack bot token');

		// Dirty the draft — the tester pauses, honestly.
		await editFirstRulePath(user, inDialog, 'x');
		expect(await inDialog.findByTestId('rule-tester-disabled-note')).toBeInTheDocument();

		// The permissions read starts failing and a refetch lands: the error alert
		// replaces the editor, and its unmount must retract the dirty report.
		worker.use(
			createErrorHandler('get', '/credentials/:cid/agents/:aid/permissions', {
				status: 500,
			}),
		);
		await queryClient.invalidateQueries({
			queryKey: agentsKeysForTest.bindingPermissions('agnt_active_1', 'cred_slack_1'),
		});

		expect(
			await inDialog.findByText("Failed to load this binding's rules."),
		).toBeInTheDocument();
		await waitFor(() => {
			expect(inDialog.queryByTestId('rule-tester-disabled-note')).not.toBeInTheDocument();
		});
		expect(inDialog.getByLabelText('Request path')).toBeEnabled();
	});

	// --- Unbind vs delete: two verbs, two blast radii -------------------------

	it('unbind (via the standard confirm dialog) removes the tile and closes the sidebar', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);

		// The danger-zone verb opens the app's standard confirm — scoped to THIS agent
		// and honest about both blast radii.
		await user.click(
			inDialog.getByRole('button', { name: 'Unbind Slack bot token from support-agent' }),
		);
		const confirm = await screen.findByRole('dialog', {
			name: 'Unbind from support-agent',
		});
		expect(confirm).toHaveTextContent(/binding and its rules are deleted/i);
		expect(confirm).toHaveTextContent(/survives for every other agent/i);
		await user.click(within(confirm).getByRole('button', { name: 'Unbind' }));

		// The binding is gone: sidebar closes, tile leaves the grid, the
		// other binding's tile survives.
		await waitFor(() => {
			expect(screen.queryByRole('dialog', { name: 'Slack' })).not.toBeInTheDocument();
		});
		await waitFor(() => {
			expect(screen.queryByText('Slack bot token')).not.toBeInTheDocument();
		});
		expect(screen.getByText('GitHub PAT')).toBeInTheDocument();
	});

	it('cancelling the unbind confirm keeps the binding and the sidebar', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		await user.click(
			within(dialog).getByRole('button', {
				name: 'Unbind Slack bot token from support-agent',
			}),
		);
		const confirm = await screen.findByRole('dialog', {
			name: 'Unbind from support-agent',
		});
		await user.click(within(confirm).getByRole('button', { name: 'Cancel' }));

		await waitFor(() => {
			expect(
				screen.queryByRole('dialog', { name: 'Unbind from support-agent' }),
			).not.toBeInTheDocument();
		});
		// Nothing was destroyed: the sidebar and the tile both survive.
		expect(screen.getByRole('dialog', { name: 'Slack' })).toBeInTheDocument();
		expect(tileOpener('Slack')).toBeInTheDocument();
	});

	it('the danger zone carries the destructive treatment and only the two destructive verbs', async () => {
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const zone = within(dialog).getByTestId('sidebar-danger-zone');

		// The shared danger-zone grammar: a danger-tinted shell around the
		// section (cheap class assertion — the treatment, not the palette).
		expect(within(zone).getByText('Danger zone')).toBeInTheDocument();
		expect(zone.querySelector('[class*="bg-danger"]')).not.toBeNull();

		// Unbind + delete live here; suspend does NOT (it moved to the header).
		expect(
			within(zone).getByRole('button', {
				name: 'Unbind Slack bot token from support-agent',
			}),
		).toBeInTheDocument();
		expect(
			within(zone).getByRole('button', {
				name: 'Delete credential Slack bot token org-wide',
			}),
		).toBeInTheDocument();
		expect(
			within(zone).queryByRole('button', { name: /Suspend|Pause/ }),
		).not.toBeInTheDocument();
	});

	it('org-wide delete confirm names every bound agent, then closes the sidebar', async () => {
		const user = userEvent.setup();
		// The credential also serves ANOTHER agent — the org-delete blast
		// radius the confirm must name (GET /credentials/{id}/agents).
		seedCredentialBindings([
			{
				agent_id: 'agnt_disabled_1',
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				serves: [{ api_vendor: 'slack.com', api_name: null, api_version: null }],
			},
		]);
		renderPage();
		await screen.findByText('1 access rule');

		const sidebar = await openSidebar('Slack');
		await user.click(
			within(sidebar).getByRole('button', {
				name: 'Delete credential Slack bot token org-wide',
			}),
		);

		const confirm = await screen.findByRole('dialog', { name: 'Delete credential' });
		const inConfirm = within(confirm);
		// The blast radius names names — including the other agent.
		expect(await inConfirm.findByText(/legacy-scraper/)).toBeInTheDocument();
		expect(inConfirm.getByText(/support-agent \(this agent\)/)).toBeInTheDocument();
		expect(
			inConfirm.getByText('2 agents use this credential and will lose access to it.'),
		).toBeInTheDocument();
		expect(inConfirm.getByText('2 bound agents')).toBeInTheDocument();
		// The delete leaves bindings behind, so it must not promise to remove them.
		expect(inConfirm.queryByText(/will also remove/i)).not.toBeInTheDocument();

		// The destructive verb sits behind the type-to-confirm gate.
		const confirmButton = inConfirm.getByRole('button', { name: 'Delete credential' });
		expect(confirmButton).toBeDisabled();
		await user.type(inConfirm.getByLabelText(/Type .* to confirm/), 'delete');
		await user.click(confirmButton);
		expect(await screen.findByText('Credential deleted')).toBeInTheDocument();
		await waitFor(() => {
			expect(screen.queryByRole('dialog', { name: 'Slack' })).not.toBeInTheDocument();
		});
	});

	it('org-wide delete refreshes OTHER agents’ binding caches — no ghost tiles', async () => {
		const user = userEvent.setup();
		// cred_slack_1 also serves legacy-scraper, and the flat surface keeps every
		// agent's binding query MOUNTED, so only an org-wide prefix sweep refreshes the
		// other agent's slice; a this-agent-only one leaves a ghost tile behind.
		seedCredentialBindings([
			{
				agent_id: 'agnt_disabled_1',
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				serves: [{ api_vendor: 'slack.com', api_name: null, api_version: null }],
			},
		]);
		const { queryClient } = renderPage();
		await screen.findByText('1 access rule');

		// The strip has loaded the OTHER agent's binding list into the cache.
		const otherAgentKey = agentsKeysForTest.credentialBindings('agnt_disabled_1');
		await waitFor(() => {
			expect(queryClient.getQueryData(otherAgentKey)).toHaveLength(1);
		});

		const sidebar = await openSidebar('Slack');
		await user.click(
			within(sidebar).getByRole('button', {
				name: 'Delete credential Slack bot token org-wide',
			}),
		);
		const confirm = await screen.findByRole('dialog', { name: 'Delete credential' });
		await user.type(within(confirm).getByLabelText(/Type .* to confirm/), 'delete');
		await user.click(within(confirm).getByRole('button', { name: 'Delete credential' }));
		expect(await screen.findByText('Credential deleted')).toBeInTheDocument();

		// The other agent's still-mounted binding query refetched to empty —
		// the regression kept the deleted credential's row here forever.
		await waitFor(() => {
			expect(queryClient.getQueryData(otherAgentKey)).toEqual([]);
		});

		// And the surface tells the same story: selecting the other agent
		// shows the honest empty state, not a ghost Slack tile.
		await user.click(screen.getByRole('tab', { name: /legacy-scraper/ }));
		expect(await screen.findByText('legacy-scraper can reach nothing yet')).toBeInTheDocument();
		expect(screen.queryByText('Slack bot token')).not.toBeInTheDocument();
	});

	// --- Suspend / resume ------------------------------------------------------

	it('the suspend control sits in the sheet header — visible without scrolling', async () => {
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const header = dialog.querySelector('[data-sheet-header]') as HTMLElement;
		expect(header).not.toBeNull();

		// The reversible cut-off lives beside the title/status line, not in
		// the danger zone.
		expect(
			within(header).getByRole('button', { name: 'Suspend binding for Slack bot token' }),
		).toBeInTheDocument();
	});

	it('a Blocked tile opens the sheet on its rules editor with "Add rule" focused', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('Suspended · not serving');
		// Resume the seeded (rule-less) GitHub binding from its tile: it then has
		// no rules, so it must read Blocked — not Ready — and say it only once.
		await user.click(screen.getByRole('button', { name: 'Resume GitHub access' }));
		const blocked = await screen.findByTestId('tile-status-blocked');
		const githubTile = tileOpener('GitHub').closest('[data-testid="api-tile"]') as HTMLElement;
		expect(within(githubTile).queryByText('Ready')).not.toBeInTheDocument();
		expect(
			within(githubTile).queryByText('No rules — all calls blocked'),
		).not.toBeInTheDocument();

		await user.click(blocked);
		const dialog = await screen.findByRole('dialog', { name: 'GitHub' });
		expect(within(dialog).getByTestId('sidebar-status-chip')).toHaveAttribute(
			'data-status',
			'blocked-no-rules',
		);
		await waitFor(() => {
			expect(within(dialog).getByRole('button', { name: 'Add rule' })).toHaveFocus();
		});
	});

	it('resume from the header clears the suspended state on the tile', async () => {
		const user = userEvent.setup();
		renderPage();
		// The seeded GitHub binding is suspended.
		await screen.findByText('Suspended · not serving');

		const dialog = await openSidebar('GitHub');
		const inDialog = within(dialog);

		// The suspended state reads in the header: status chip + Resume control.
		const chip = await inDialog.findByTestId('sidebar-status-chip');
		expect(chip).toHaveTextContent('Suspended');
		expect(chip).toHaveAttribute('data-status', 'suspended');
		const header = dialog.querySelector('[data-sheet-header]') as HTMLElement;
		await user.click(
			within(header).getByRole('button', { name: 'Resume binding for GitHub PAT' }),
		);

		// The suspension clears in the sidebar AND on the tile underneath — and,
		// with no rules on this binding, both say Blocked, never Ready.
		await waitFor(() => {
			expect(inDialog.getByTestId('sidebar-status-chip')).toHaveAttribute(
				'data-status',
				'blocked-no-rules',
			);
		});
		await waitFor(() => {
			expect(screen.queryByText('Suspended · not serving')).not.toBeInTheDocument();
		});
		const githubTile = tileOpener('GitHub').closest('[data-testid="api-tile"]') as HTMLElement;
		expect(within(githubTile).getByTestId('tile-status-chip')).toHaveTextContent(
			'Blocked · no rules',
		);
		expect(within(githubTile).queryByText('Ready')).not.toBeInTheDocument();
	});

	it('suspend from the header reflects on the tile and round-trips back to serving', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		const inDialog = within(dialog);

		// Suspend is reversible and safe — one labelled click, no confirm.
		await user.click(
			inDialog.getByRole('button', { name: 'Suspend binding for Slack bot token' }),
		);

		// The header flips to the suspended treatment with its resume…
		await waitFor(() => {
			expect(inDialog.getByTestId('sidebar-status-chip')).toHaveAttribute(
				'data-status',
				'suspended',
			);
		});
		const resumeButton = inDialog.getByRole('button', {
			name: 'Resume binding for Slack bot token',
		});
		expect(resumeButton).toBeInTheDocument();
		// …and the tile underneath tells the same story. Scoped to the Slack
		// tile — the seeded GitHub tile carries the same suspended line.
		const slackTile = tileOpener('Slack').closest('[data-testid="api-tile"]') as HTMLElement;
		expect(await within(slackTile).findByText('Suspended · not serving')).toBeInTheDocument();

		// Round-trip: resume restores the serving state everywhere.
		await user.click(resumeButton);
		await waitFor(() => {
			expect(inDialog.getByTestId('sidebar-status-chip')).toHaveAttribute(
				'data-status',
				'ready',
			);
		});
		expect(
			inDialog.getByRole('button', { name: 'Suspend binding for Slack bot token' }),
		).toBeInTheDocument();
		await waitFor(() => {
			expect(
				within(slackTile).queryByText('Suspended · not serving'),
			).not.toBeInTheDocument();
		});
	});

	// --- Dashed (awaiting-consent) tiles ---------------------------------------

	it('a dashed tile opens the sidebar with the Finish-connecting affordance', async () => {
		const user = userEvent.setup();
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
			}),
			makeMockCredential({
				credential_id: 'cred_github_1',
				name: 'GitHub PAT',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
			}),
			// Interactive OAuth whose consent round-trip has not completed.
			makeMockCredential({
				credential_id: 'cred_stripe_oauth',
				name: 'Stripe OAuth',
				type: CredentialType.OAUTH2,
				api: { vendor: 'stripe.com', name: 'default', version: '1.0.0' },
				details: { grant_type: 'authorization_code', connected: false },
			}),
		]);
		seedCredentialBindings([
			{
				agent_id: 'agnt_active_1',
				credential_id: 'cred_stripe_oauth',
				name: 'Stripe OAuth',
				serves: [{ api_vendor: 'stripe.com', api_name: null, api_version: null }],
			},
		]);

		renderPage();
		await screen.findByText(/Sign-in at stripe\.com unfinished/);

		// The tile's own "Finish connecting" line opens the SAME sidebar (the
		// connect flow lives there) — not a separate popup.
		await user.click(screen.getByRole('button', { name: /Finish connecting/ }));
		// Named by the tile's friendly title (the shared humaniser's), not the
		// raw vendor slug the binding stores.
		const dialog = await screen.findByRole('dialog', { name: 'Stripe.Com' });
		const inDialog = within(dialog);

		const affordance = await inDialog.findByTestId('sidebar-connect-affordance');
		expect(affordance).toHaveTextContent(/Waiting for a sign-in at stripe\.com/);
		expect(
			within(affordance).getByRole('button', { name: /Finish connecting/ }),
		).toBeInTheDocument();

		// Even an awaiting-consent binding is a REAL binding — the rules
		// editor and tester render, no empty branch.
		expect(await inDialog.findByText('Permission rules for Stripe OAuth')).toBeInTheDocument();
		expect(inDialog.getByText('Test a request')).toBeInTheDocument();
	});

	// --- Nested edit sheet -------------------------------------------------------

	it('Edit credential stacks the shared edit sheet; Escape closes only the top sheet', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');

		const dialog = await openSidebar('Slack');
		await user.click(within(dialog).getByRole('button', { name: /Edit credential/ }));

		const editSheet = await screen.findByRole('dialog', { name: 'Edit credential' });
		expect(within(editSheet).getByDisplayValue('Slack bot token')).toBeInTheDocument();
		// Wait for the stacked sheet's enter animation (it focuses its own
		// Close button) so Escape is armed on the TOP sheet.
		await waitFor(() => {
			expect(within(editSheet).getByRole('button', { name: 'Close' })).toHaveFocus();
		});

		// Escape peels ONE layer: the edit sheet goes, the sidebar stays.
		await user.keyboard('{Escape}');
		await waitFor(() => {
			expect(
				screen.queryByRole('dialog', { name: 'Edit credential' }),
			).not.toBeInTheDocument();
		});
		expect(screen.getByRole('dialog', { name: 'Slack' })).toBeInTheDocument();
	});

	// --- Ephemeral state: agent switch closes the sidebar -------------------------

	it('selecting a different agent closes the sidebar', async () => {
		const user = userEvent.setup();
		renderPage();
		await screen.findByText('1 access rule');
		await openSidebar('Slack');

		await user.click(screen.getByRole('tab', { name: /legacy-scraper/ }));
		await waitFor(() => {
			expect(screen.queryByRole('dialog', { name: 'Slack' })).not.toBeInTheDocument();
		});
	});

	it('has no critical a11y violations with the sidebar open', async () => {
		const { container } = renderPage();
		await screen.findByText('1 access rule');
		const dialog = await openSidebar('Slack');
		await within(dialog).findByText('Permission rules for Slack bot token');
		// Let the sheet's entrance animation settle for axe's contrast checks.
		await new Promise((resolve) => setTimeout(resolve, 400));
		await checkA11y(container);
	});
});

describe('ApiAccessSidebar — a credential shared with the viewer', () => {
	const VIEWER = 'usr_viewer_1';

	beforeEach(async () => {
		await page.viewport(1280, 900);
		setToken('test-token');
		resetAgentsStore();
		seedComposedStores();
		// Slack is someone else's credential; GitHub is the viewer's own.
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_slack_1',
				name: 'Slack bot token',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'slack.com', name: 'default', version: '1.0.0' },
				created_by: 'usr_someone_else',
			}),
			makeMockCredential({
				credential_id: 'cred_github_1',
				name: 'GitHub PAT',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
				created_by: VIEWER,
			}),
		]);
		worker.use(
			http.get('/users/me', () =>
				HttpResponse.json({
					id: VIEWER,
					email: 'viewer@local',
					first_name: 'View',
					last_name: 'Er',
					active: true,
					// A member: manages agents and credentials, but is not an org admin.
					permissions: ['agents:read', 'agents:write', 'credentials:write'],
					must_change_password: false,
					created_at: '2026-01-01T00:00:00Z',
					updated_at: null,
				}),
			),
		);
	});

	function renderAuthed() {
		return renderWithProviders(
			<AuthProvider>
				<AgentsPage />
				<Toaster />
			</AuthProvider>,
			{ route: '/?agent=agnt_active_1' },
		);
	}

	it('reads as shared with you: no Edit credential, no org-wide delete, unbind stays', async () => {
		renderAuthed();
		await screen.findByText('1 access rule');
		const inDialog = within(await openSidebar('Slack'));

		expect(inDialog.getByTestId('credential-shared-badge')).toHaveTextContent(
			'Shared with you',
		);
		expect(inDialog.queryByRole('button', { name: /Edit credential/ })).not.toBeInTheDocument();
		expect(
			inDialog.queryByRole('button', { name: 'Delete credential Slack bot token org-wide' }),
		).not.toBeInTheDocument();
		// The binding is the agent's — unbinding it is still offered.
		expect(
			inDialog.getByRole('button', { name: 'Unbind Slack bot token from support-agent' }),
		).toBeInTheDocument();
	});

	it('counts only the viewer’s agents on a shared credential', async () => {
		renderAuthed();
		await screen.findByText('1 access rule');
		const shared = within(await openSidebar('Slack'));
		const meta = await shared.findByText(/· used by /);
		expect(meta).toHaveTextContent('used by 1 of your agents');
		expect(meta).not.toHaveTextContent('this agent only');
	});

	it('keeps the unscoped count on a credential the viewer created', async () => {
		renderAuthed();
		await screen.findByText('1 access rule');
		const own = within(await openSidebar('GitHub'));
		expect(await own.findByText(/· used by /)).toHaveTextContent('used by this agent only');
	});

	it('keeps Edit and Delete on a credential the viewer created', async () => {
		renderAuthed();
		await screen.findByText('1 access rule');
		const inDialog = within(await openSidebar('GitHub'));

		expect(inDialog.queryByTestId('credential-shared-badge')).not.toBeInTheDocument();
		expect(inDialog.getByRole('button', { name: /Edit credential/ })).toBeInTheDocument();
		expect(
			inDialog.getByRole('button', { name: 'Delete credential GitHub PAT org-wide' }),
		).toBeInTheDocument();
	});

	/**
	 * Pins #1543 item 2: the case `GET /credentials` cannot answer at all.
	 *
	 * `GET /credentials` is OWNER-SCOPED for a non-admin, so another user's
	 * credential bound to this agent is simply ABSENT from the list — the tile is
	 * composed from the binding alone and carries no `credentialCreatedBy` key
	 * (not a null one: `apiTiles` spreads the field only when a credential row
	 * was found). Ownership is therefore UNKNOWN, and unknown must read as "not
	 * yours": the previous `!== undefined` guard inverted exactly this case into
	 * "editable", drew Edit and Delete, and both 404ed — Edit on a sheet with
	 * nothing to edit.
	 */
	it('withholds Edit and Delete when the credential is not in the viewer’s list at all', async () => {
		// The viewer's own list holds GitHub only. Slack's binding survives, so the
		// tile still renders — with no credential row behind it.
		resetCredentialsStore([
			makeMockCredential({
				credential_id: 'cred_github_1',
				name: 'GitHub PAT',
				type: CredentialType.BEARER_TOKEN,
				api: { vendor: 'github.com', name: 'default', version: '1.0.0' },
				created_by: VIEWER,
			}),
		]);
		renderAuthed();
		await screen.findByText('1 access rule');
		const inDialog = within(await openSidebar('Slack'));

		expect(inDialog.queryByRole('button', { name: /Edit credential/ })).not.toBeInTheDocument();
		expect(
			inDialog.queryByRole('button', { name: /Delete credential .* org-wide/ }),
		).not.toBeInTheDocument();
		// Unbinding is the agent's own business and stays — the binding is readable.
		expect(
			inDialog.getByRole('button', { name: /^Unbind .* from support-agent$/ }),
		).toBeInTheDocument();
	});
});
