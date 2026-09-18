/**
 * CreateCredentialFlow — the shell `surface` switches, and nothing else.
 *
 * The wizard's own behaviour (picker, spec-shaped form, submit path) is covered
 * where it is exercised end to end: `CredentialsPage`, `ApiSetupQueue` and
 * `CredentialInventorySheet`. These specs pin the two containers — that a drawer
 * is the default, that a host in the browser's top layer can ask for a centred
 * dialog instead, and that neither hands a stray backdrop click the power to
 * discard a half-typed secret.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { userEvent as browserUser } from 'vitest/browser';
import { renderWithProviders, screen, waitFor } from '@/__tests__/test-utils';
import { setToken } from '@/shared/api';
import { resetApisStore } from '@/shared/credentials/mocks/handlers';
import { CreateCredentialFlow } from '@/shared/credentials/components/CreateCredentialFlow';

describe('CreateCredentialFlow', () => {
	beforeEach(() => {
		setToken('test-token');
		resetApisStore([]);
	});
	afterEach(() => resetApisStore());

	it('is a drawer by default, named by its step', async () => {
		renderWithProviders(<CreateCredentialFlow open onClose={vi.fn()} onCreated={vi.fn()} />);

		const sheet = await screen.findByTestId('sheet-primitive');
		expect(sheet).toHaveAttribute('role', 'dialog');
		// The drawer's own heading names it, so assistive tech reads the step
		// rather than a generic "dialog".
		expect(screen.getByRole('dialog', { name: /Choose an API/ })).toBe(sheet);
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
		expect(screen.getByRole('heading', { name: /Choose an API/ })).toBeVisible();
	});

	it('does not close the drawer on a backdrop click, but Escape still does', async () => {
		const onClose = vi.fn();
		renderWithProviders(<CreateCredentialFlow open onClose={onClose} onCreated={vi.fn()} />);
		await screen.findByTestId('sheet-primitive');

		// A stray click outside would discard the wizard's draft — closing
		// resets it — so the backdrop is inert here.
		await browserUser.click(document.body, { position: { x: 5, y: 5 } });
		expect(onClose).not.toHaveBeenCalled();

		// Escape is a deliberate dismissal and keeps working.
		await browserUser.keyboard('{Escape}');
		await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
	});
});
