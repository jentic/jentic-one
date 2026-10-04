/**
 * CreateCredentialFlow — the shell `surface` switches, and nothing else; the
 * wizard's own behaviour is covered where it runs end to end (`ApiSetupQueue`,
 * `CredentialInventorySheet`). Pins the drawer default, the
 * top-layer host's centred dialog, that a backdrop click closes the drawer like
 * Escape (but not mid-drag, nor over a live connect), that an uploaded spec lands
 * on the credential form for the API it registered, and that a name another
 * credential for the API holds is warned about, not blocked.
 */
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { page, userEvent as browserUser } from 'vitest/browser';
import { renderWithProviders, screen, waitFor, userEvent, within } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import {
	makeMockApi,
	makeMockCredential,
	resetApisStore,
	resetCredentialsStore,
} from '@/shared/credentials/mocks/handlers';
import { CreateCredentialFlow } from '@/shared/credentials/components/CreateCredentialFlow';
import type { SelectedApi } from '@/shared/credentials/api';

const ACME = makeMockApi({ vendor: 'acme.io', name: 'main', displayName: 'Acme' });

const PINNED_ACME: SelectedApi = {
	source: 'local',
	vendor: 'acme.io',
	name: 'main',
	version: '1.0.0',
	label: 'Acme',
};

/** A credential for ACME already called `name`. */
function existingAcmeCredential(name: string) {
	return makeMockCredential({ name, api: { vendor: 'acme.io', name: 'main', version: '' } });
}

/** An import that completes on submit and registers ACME — no job polling. */
function stubCompletedImport(): void {
	worker.use(
		http.post('/apis', () =>
			HttpResponse.json(
				{ job_id: 'job_upload', status: 'completed', _links: { self: '/jobs/job_upload' } },
				{ status: 202 },
			),
		),
		http.get('/jobs/job_upload/result', () =>
			HttpResponse.json({
				revisions: [{ api: { vendor: 'acme.io', name: 'main', version: '1.0.0' } }],
			}),
		),
		http.get('/apis/acme.io/main/1.0.0', () => HttpResponse.json(ACME.row)),
	);
}

describe('CreateCredentialFlow', () => {
	beforeEach(() => {
		setToken('test-token');
		resetApisStore([]);
	});
	afterEach(() => {
		resetApisStore();
		resetCredentialsStore();
	});

	it('is a drawer by default, named by its step', async () => {
		renderWithProviders(<CreateCredentialFlow open onClose={vi.fn()} onCreated={vi.fn()} />);

		const sheet = await screen.findByTestId('sheet-primitive');
		expect(sheet).toHaveAttribute('role', 'dialog');
		// The drawer's own heading names it, so assistive tech reads the step
		// rather than a generic "dialog".
		expect(screen.getByRole('dialog', { name: /^Add credential$/ })).toBe(sheet);
		// Nothing in the browser's top layer: this is a sheet, not a modal.
		expect(document.querySelector('dialog[open]')).toBeNull();
	});

	it('renders a centred dialog when the host asks for one', async () => {
		renderWithProviders(
			<CreateCredentialFlow open onClose={vi.fn()} onCreated={vi.fn()} surface="dialog" />,
		);

		// A native modal <dialog> — the only surface reachable from a host that
		// is itself in the top layer.
		await waitFor(() => expect(document.querySelector('dialog[open]')).not.toBeNull());
		expect(screen.queryByTestId('sheet-primitive')).not.toBeInTheDocument();
		expect(screen.getByRole('heading', { name: /^Add credential$/ })).toBeVisible();
	});

	describe('backdrop dismissal', () => {
		// A phone-width drawer is full-bleed — no backdrop to click — so give the
		// drawer room to leave the dimmed page showing beside it.
		beforeEach(async () => {
			await page.viewport(1280, 800);
		});
		afterEach(async () => {
			await page.viewport(414, 896);
		});

		/**
		 * Resolve once the drawer has finished opening. The sheet ignores a backdrop
		 * click during its entrance (it only dismisses once `open`, which lands a
		 * double rAF after mount), so a click fired as soon as the sheet is in the
		 * DOM can race it under load — and a "stays open" assertion would then pass
		 * for the wrong reason. The backdrop reaches full opacity only once `open`,
		 * and must be what the pointer hits at the click point, not the panel.
		 */
		async function drawerSettled(): Promise<HTMLElement> {
			const backdrop = await screen.findByTestId('sheet-backdrop');
			await waitFor(() => {
				expect(getComputedStyle(backdrop).opacity).toBe('1');
				expect(document.elementFromPoint(5, 5)).toBe(backdrop);
			});
			return backdrop;
		}

		/** Click the dimmed backdrop well clear of the drawer (top-left corner). */
		const clickBackdrop = async (): Promise<void> => {
			const backdrop = await drawerSettled();
			await browserUser.click(backdrop, { position: { x: 5, y: 5 } });
		};

		/** A host that owns `open`, like the real ones, so a close can be reopened. */
		function Host() {
			const [open, setOpen] = useState(true);
			return (
				<>
					<button type="button" onClick={(): void => setOpen(true)}>
						Reopen
					</button>
					<CreateCredentialFlow
						open={open}
						onClose={(): void => setOpen(false)}
						onCreated={vi.fn()}
						initialApi={PINNED_ACME}
					/>
				</>
			);
		}

		it('closes the drawer on a backdrop click', async () => {
			const onClose = vi.fn();
			renderWithProviders(
				<CreateCredentialFlow open onClose={onClose} onCreated={vi.fn()} />,
			);
			await screen.findByTestId('sheet-primitive');

			await clickBackdrop();
			await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
		});

		it.each([
			['Escape', (): Promise<void> => browserUser.keyboard('{Escape}')],
			['the backdrop', clickBackdrop],
		])('treats the draft the same when closed via %s', async (_how, close) => {
			resetApisStore([ACME]);
			const user = userEvent.setup();
			renderWithProviders(<Host />);
			const name = await screen.findByLabelText(/^Name/);
			await waitFor(() => expect(name).toHaveValue('Acme'));
			await user.clear(name);
			await user.type(name, 'Draft name');

			await close();
			await waitFor(() =>
				expect(screen.queryByTestId('sheet-primitive')).not.toBeInTheDocument(),
			);

			// Every close of this flow wipes the draft (it can hold a secret), so
			// reopening lands on the seeded form — the same for either dismissal.
			await user.click(screen.getByRole('button', { name: 'Reopen' }));
			await waitFor(() => expect(screen.getByLabelText(/^Name/)).toHaveValue('Acme'));
		});

		it('does not close on a drag that starts inside and ends on the backdrop', async () => {
			resetApisStore([ACME]);
			const onClose = vi.fn();
			renderWithProviders(
				<CreateCredentialFlow
					open
					onClose={onClose}
					onCreated={vi.fn()}
					initialApi={PINNED_ACME}
				/>,
			);
			const name = await screen.findByLabelText(/^Name/);
			const backdrop = await drawerSettled();
			const upOnBackdrop = vi.fn();
			backdrop.addEventListener('mouseup', upOnBackdrop);

			// A real mousedown in the field and mouseup over the backdrop — the
			// text-selection overshoot — must not read as a click outside.
			await browserUser.dragAndDrop(name, backdrop, {
				targetPosition: { x: 5, y: 5 },
			});
			// The drag really did end on the backdrop — and that mouseup's click (if
			// any) is dispatched in the same task, so the sheet has already had its
			// chance to close…
			await expect.poll(() => upOnBackdrop).toHaveBeenCalled();
			// …yet the browser dispatches that click to the common ancestor, not the
			// backdrop, so the drawer stays open.
			expect(onClose).not.toHaveBeenCalled();
			expect(screen.getByTestId('sheet-primitive')).toBeInTheDocument();
		});

		it('keeps the backdrop inert while a connect session is live, but Escape still closes', async () => {
			const onClose = vi.fn();
			renderWithProviders(
				<CreateCredentialFlow
					open
					onClose={onClose}
					onCreated={vi.fn()}
					approvalSession={{ sessionId: 'sess_x', pollToken: 'tok_x' }}
				/>,
			);
			const backdrop = await drawerSettled();
			const clickOnBackdrop = vi.fn();
			backdrop.addEventListener('click', clickOnBackdrop);

			// Closing unmounts the connect, which cancels its session — a stray
			// click must not abandon a sign-in in progress.
			await clickBackdrop();
			// The click reached the backdrop of a fully open drawer (whose own
			// handler runs in that same dispatch), and still didn't close it.
			await expect.poll(() => clickOnBackdrop).toHaveBeenCalledTimes(1);
			expect(onClose).not.toHaveBeenCalled();

			await browserUser.keyboard('{Escape}');
			await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
		});
	});

	it('offers an upload from the pick step', async () => {
		renderWithProviders(<CreateCredentialFlow open onClose={vi.fn()} onCreated={vi.fn()} />);
		await screen.findByRole('dialog', { name: /^Add credential$/ });

		// Reachable before any search: an operator who knows the API isn't
		// catalogued shouldn't have to prove it first.
		expect(screen.getByRole('button', { name: 'Upload an API' })).toBeVisible();
	});

	it('lands on the credential form for the API an upload registered', async () => {
		stubCompletedImport();
		const user = userEvent.setup();
		renderWithProviders(<CreateCredentialFlow open onClose={vi.fn()} onCreated={vi.fn()} />);
		await screen.findByRole('dialog', { name: /^Add credential$/ });

		await user.click(screen.getByRole('button', { name: 'Upload an API' }));
		await waitFor(() => expect(screen.getByTestId('import-spec-dialog')).toBeVisible());
		await user.type(screen.getByTestId('import-spec-url'), 'https://acme.io/openapi.json');
		await user.click(screen.getByTestId('import-spec-submit'));

		await waitFor(() =>
			expect(screen.getByRole('dialog', { name: /Add credential — Acme/ })).toBeVisible(),
		);
		expect(screen.getByTestId('selected-api-summary')).toHaveTextContent('Acme');
		await waitFor(() => expect(screen.getByTestId('import-spec-dialog')).not.toBeVisible());
	});

	it('defaults a setup-queue (pinnedApi) API to any version, offering its version as the pin', async () => {
		resetApisStore([ACME]);
		renderWithProviders(
			<CreateCredentialFlow
				open
				onClose={vi.fn()}
				onCreated={vi.fn()}
				pinnedApi={PINNED_ACME}
			/>,
		);

		const summary = await screen.findByTestId('selected-api-summary');
		// The create body carries no version until the operator pins one.
		expect(summary).toHaveTextContent('acme.io/main · any version');
		expect(summary).not.toHaveTextContent('@1.0.0');
		const toggle = screen.getByRole('group', { name: 'Use this credential for' });
		expect(within(toggle).getByRole('button', { name: 'Any version' })).toHaveAttribute(
			'aria-pressed',
			'true',
		);
		expect(within(toggle).getByRole('button', { name: 'Version 1.0.0' })).toHaveAttribute(
			'aria-pressed',
			'false',
		);
	});

	it('offers no version pin for a catalog API — its version is not the registry’s yet', async () => {
		renderWithProviders(
			<CreateCredentialFlow
				open
				onClose={vi.fn()}
				onCreated={vi.fn()}
				pinnedApi={{ ...PINNED_ACME, source: 'catalog', registered: false }}
			/>,
		);

		const summary = await screen.findByTestId('selected-api-summary');
		expect(summary).toHaveTextContent('acme.io/main · any version');
		expect(
			screen.queryByRole('group', { name: 'Use this credential for' }),
		).not.toBeInTheDocument();
	});

	describe('a name another credential for the API holds', () => {
		beforeEach(() => resetApisStore([ACME]));

		it('keeps the default name and suggests a free one', async () => {
			resetCredentialsStore([existingAcmeCredential('Acme')]);
			renderWithProviders(
				<CreateCredentialFlow
					open
					onClose={vi.fn()}
					onCreated={vi.fn()}
					pinnedApi={PINNED_ACME}
				/>,
			);

			const name = await screen.findByLabelText(/^Name/);
			const clash = await screen.findByTestId('credential-name-clash');
			expect(name).toHaveValue('Acme');
			expect(clash).toHaveTextContent('Suggested: Acme 2');
		});

		it('warns on a typed duplicate and offers a free name, without blocking', async () => {
			resetCredentialsStore([existingAcmeCredential('Production')]);
			const user = userEvent.setup();
			renderWithProviders(
				<CreateCredentialFlow
					open
					onClose={vi.fn()}
					onCreated={vi.fn()}
					pinnedApi={PINNED_ACME}
				/>,
			);

			const name = await screen.findByLabelText(/^Name/);
			await user.clear(name);
			await user.type(name, 'production');

			const clash = await screen.findByTestId('credential-name-clash');
			expect(clash).toHaveTextContent('You already have a credential named Production');
			expect(clash).toHaveTextContent('Suggested: production 2');
			expect(name).toHaveAttribute('aria-describedby', clash.id);
			// A warning, not an error: the field stays valid and editable.
			expect(name).not.toHaveAttribute('aria-invalid');

			await user.click(screen.getByRole('button', { name: 'Use this name' }));
			expect(name).toHaveValue('production 2');
			expect(screen.queryByTestId('credential-name-clash')).not.toBeInTheDocument();
		});
	});

	describe('a preselected API (initialApi)', () => {
		beforeEach(() => resetApisStore([ACME]));

		it('opens on the form for that API, not the picker', async () => {
			renderWithProviders(
				<CreateCredentialFlow
					open
					onClose={vi.fn()}
					onCreated={vi.fn()}
					initialApi={PINNED_ACME}
				/>,
			);

			// Step 2 straight away: the host already knows the API.
			expect(
				await screen.findByRole('dialog', { name: 'Add credential — Acme' }),
			).toBeVisible();
			expect(screen.getByText(/Step 2 of 2/)).toBeVisible();
			expect(await screen.findByTestId('selected-api-summary')).toHaveTextContent(
				'acme.io/main',
			);
			expect(screen.getByLabelText(/^Name/)).toHaveValue('Acme');
			expect(screen.queryByPlaceholderText(/Search APIs/)).not.toBeInTheDocument();
		});

		it('still lets the operator change the API — Change returns to the picker', async () => {
			const user = userEvent.setup();
			renderWithProviders(
				<CreateCredentialFlow
					open
					onClose={vi.fn()}
					onCreated={vi.fn()}
					initialApi={PINNED_ACME}
				/>,
			);
			await screen.findByTestId('selected-api-summary');
			// Unlike a pinned API, both ways back to step 1 are offered.
			expect(screen.getByRole('button', { name: 'Back' })).toBeVisible();

			await user.click(screen.getByRole('button', { name: 'Change' }));
			expect(await screen.findByRole('dialog', { name: /^Add credential$/ })).toBeVisible();
			expect(screen.getByText(/Step 1 of 2/)).toBeVisible();
		});

		it('starts from the preselected API again on each reopen', async () => {
			const user = userEvent.setup();
			const props = { onClose: vi.fn(), onCreated: vi.fn(), initialApi: PINNED_ACME };
			const { rerender } = renderWithProviders(<CreateCredentialFlow open {...props} />);
			await screen.findByTestId('selected-api-summary');
			await user.click(screen.getByRole('button', { name: 'Change' }));
			await screen.findByRole('dialog', { name: /^Add credential$/ });

			rerender(<CreateCredentialFlow open={false} {...props} />);
			rerender(<CreateCredentialFlow open {...props} />);
			expect(
				await screen.findByRole('dialog', { name: 'Add credential — Acme' }),
			).toBeVisible();
		});
	});

	describe('the version a credential is saved for', () => {
		/** Every `POST /credentials` body's `api`, passing through to the mock. */
		function recordCreatedApis(): unknown[] {
			const apis: unknown[] = [];
			worker.events.on('request:start', ({ request }) => {
				if (request.method === 'POST' && new URL(request.url).pathname === '/credentials')
					void request
						.clone()
						.json()
						.then((b: { api?: unknown }) => apis.push(b.api));
			});
			return apis;
		}

		async function submitApiKey(user: ReturnType<typeof userEvent.setup>): Promise<void> {
			await user.type(await screen.findByLabelText(/^API key/), 'sk_test_123');
			await user.click(screen.getByRole('button', { name: 'Create credential' }));
		}

		const versionToggle = (): HTMLElement =>
			screen.getByRole('group', { name: 'Use this credential for' });

		beforeEach(() => resetApisStore([ACME]));
		afterEach(() => worker.events.removeAllListeners());

		it('defaults a flow opened from one API to any version', async () => {
			const user = userEvent.setup();
			const apis = recordCreatedApis();
			const onCreated = vi.fn();
			renderWithProviders(
				<CreateCredentialFlow
					open
					onClose={vi.fn()}
					onCreated={onCreated}
					initialApi={PINNED_ACME}
				/>,
			);

			const summary = await screen.findByTestId('selected-api-summary');
			expect(summary).toHaveTextContent('acme.io/main · any version');
			expect(
				within(versionToggle()).getByRole('button', { name: 'Any version' }),
			).toHaveAttribute('aria-pressed', 'true');

			await submitApiKey(user);
			await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
			// Unpinned: no version on the wire (the backend's wildcard).
			expect(apis).toEqual([{ vendor: 'acme.io', name: 'main' }]);
		});

		it("lets the operator pin it to the API's version instead", async () => {
			const user = userEvent.setup();
			const apis = recordCreatedApis();
			const onCreated = vi.fn();
			renderWithProviders(
				<CreateCredentialFlow
					open
					onClose={vi.fn()}
					onCreated={onCreated}
					initialApi={PINNED_ACME}
				/>,
			);
			await screen.findByTestId('selected-api-summary');

			await user.click(
				within(versionToggle()).getByRole('button', { name: 'Version 1.0.0' }),
			);
			expect(screen.getByTestId('selected-api-summary')).toHaveTextContent(
				'acme.io/main@1.0.0',
			);
			expect(
				within(versionToggle()).getByRole('button', { name: 'Version 1.0.0' }),
			).toHaveAttribute('aria-pressed', 'true');

			await submitApiKey(user);
			await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
			expect(apis).toEqual([{ vendor: 'acme.io', name: 'main', version: '1.0.0' }]);
		});

		it('offers the same picker for a workspace API picked in step 1, on any version', async () => {
			const user = userEvent.setup();
			const apis = recordCreatedApis();
			const onCreated = vi.fn();
			renderWithProviders(
				<CreateCredentialFlow open onClose={vi.fn()} onCreated={onCreated} />,
			);

			await user.click(await screen.findByRole('button', { name: /Acme/ }));
			const summary = await screen.findByTestId('selected-api-summary');
			expect(summary).toHaveTextContent('acme.io/main · any version');
			expect(
				within(versionToggle()).getByRole('button', { name: 'Any version' }),
			).toHaveAttribute('aria-pressed', 'true');
			expect(
				within(versionToggle()).getByRole('button', { name: 'Version 1.0.0' }),
			).toBeVisible();

			await submitApiKey(user);
			await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
			expect(apis).toEqual([{ vendor: 'acme.io', name: 'main' }]);
		});

		it('resets to any version when the operator re-picks via Change', async () => {
			const user = userEvent.setup();
			renderWithProviders(
				<CreateCredentialFlow
					open
					onClose={vi.fn()}
					onCreated={vi.fn()}
					initialApi={PINNED_ACME}
				/>,
			);
			await screen.findByTestId('selected-api-summary');
			await user.click(
				within(versionToggle()).getByRole('button', { name: 'Version 1.0.0' }),
			);
			expect(screen.getByTestId('selected-api-summary')).toHaveTextContent('@1.0.0');

			await user.click(screen.getByRole('button', { name: 'Change' }));
			await user.click(await screen.findByRole('button', { name: /Acme/ }));

			// A pick in step 1 is a pick like any other — even of the same API.
			expect(await screen.findByTestId('selected-api-summary')).toHaveTextContent(
				'acme.io/main · any version',
			);
			expect(
				within(versionToggle()).getByRole('button', { name: 'Any version' }),
			).toHaveAttribute('aria-pressed', 'true');
		});

		it('returns to any version when the flow is reopened', async () => {
			const user = userEvent.setup();
			const props = { onClose: vi.fn(), onCreated: vi.fn(), initialApi: PINNED_ACME };
			const { rerender } = renderWithProviders(<CreateCredentialFlow open {...props} />);
			await screen.findByTestId('selected-api-summary');
			await user.click(
				within(versionToggle()).getByRole('button', { name: 'Version 1.0.0' }),
			);

			rerender(<CreateCredentialFlow open={false} {...props} />);
			rerender(<CreateCredentialFlow open {...props} />);
			expect(await screen.findByTestId('selected-api-summary')).toHaveTextContent(
				'acme.io/main · any version',
			);
		});
	});

	it('starts on the picker when the host names no API', async () => {
		renderWithProviders(<CreateCredentialFlow open onClose={vi.fn()} onCreated={vi.fn()} />);
		await screen.findByRole('dialog', { name: /^Add credential$/ });
		expect(screen.getByText(/Step 1 of 2/)).toBeVisible();
		expect(screen.queryByTestId('selected-api-summary')).not.toBeInTheDocument();
	});
});
