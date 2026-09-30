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
import { renderWithProviders, screen, waitFor, userEvent } from '@/__tests__/test-utils';
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

		/** Click the dimmed backdrop well clear of the drawer (top-left corner). */
		const clickBackdrop = (): Promise<void> =>
			browserUser.click(screen.getByTestId('sheet-backdrop'), {
				position: { x: 5, y: 5 },
			});

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
			const backdrop = screen.getByTestId('sheet-backdrop');
			const upOnBackdrop = vi.fn();
			backdrop.addEventListener('mouseup', upOnBackdrop);

			// A real mousedown in the field and mouseup over the backdrop — the
			// text-selection overshoot — must not read as a click outside.
			await browserUser.dragAndDrop(name, backdrop, {
				targetPosition: { x: 5, y: 5 },
			});
			await new Promise((r) => setTimeout(r, 50));
			// The drag really did end on the backdrop…
			expect(upOnBackdrop).toHaveBeenCalled();
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
			await screen.findByTestId('sheet-primitive');

			// Closing unmounts the connect, which cancels its session — a stray
			// click must not abandon a sign-in in progress.
			await clickBackdrop();
			await new Promise((r) => setTimeout(r, 50));
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

	it('shows "any version" for a picked API, since the saved credential is unpinned', async () => {
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
		// The picked API reports 1.0.0, but the create body carries no version —
		// the banner must not claim a pin that isn't sent.
		expect(summary).toHaveTextContent('acme.io/main · any version');
		expect(summary).not.toHaveTextContent('@1.0.0');
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

	it('starts on the picker when the host names no API', async () => {
		renderWithProviders(<CreateCredentialFlow open onClose={vi.fn()} onCreated={vi.fn()} />);
		await screen.findByRole('dialog', { name: /^Add credential$/ });
		expect(screen.getByText(/Step 1 of 2/)).toBeVisible();
		expect(screen.queryByTestId('selected-api-summary')).not.toBeInTheDocument();
	});
});
