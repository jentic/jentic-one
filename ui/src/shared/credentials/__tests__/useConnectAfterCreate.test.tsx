import { describe, it, expect, beforeEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import { renderWithProviders, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { worker } from '@/mocks/browser';
import { setToken } from '@/shared/api';
import { Button, clearAllToasts, Toaster } from '@/shared/ui';
import { CredentialType, type ConnectOutcome } from '@/shared/credentials/api';
import { useConnectAfterCreate } from '@/shared/credentials/components/useConnectAfterCreate';
import type { CreatedCredentialInfo } from '@/shared/credentials/components/CreateCredentialFlow';

/**
 * The one connect-after-create / standalone-connect policy every credential
 * surface runs: a just-created OAuth credential whose sign-in is abandoned is
 * DISCARDED (`redirected` excepted — the user is mid-flow), while connecting a
 * listed credential keeps it whatever happens.
 */

const OAUTH: CreatedCredentialInfo = {
	credentialId: 'cred_new',
	name: 'Acme OAuth',
	type: CredentialType.OAUTH2,
	provider: 'acme',
	needsConnect: true,
} as CreatedCredentialInfo;

let deleted: string[] = [];

function Host({
	outcome,
	info = OAUTH,
}: {
	outcome: ConnectOutcome | 'throw';
	info?: CreatedCredentialInfo;
}) {
	const { afterCreate, connectExisting } = useConnectAfterCreate({
		connect: async () => {
			if (outcome === 'throw') throw new Error('popup blew up');
			return outcome;
		},
	});
	return (
		<>
			<Button onClick={() => afterCreate(info)}>created</Button>
			<Button onClick={() => void connectExisting('cred_listed', 'Listed')}>connect</Button>
			<Toaster />
		</>
	);
}

describe('useConnectAfterCreate', () => {
	beforeEach(() => {
		setToken('test-token');
		clearAllToasts();
		deleted = [];
		worker.use(
			http.delete('/credentials/:id', ({ params }) => {
				deleted.push(String(params.id));
				return new HttpResponse(null, { status: 204 });
			}),
		);
	});

	it.each([
		[{ status: 'cancelled' } as const, 'Sign-in cancelled'],
		[{ status: 'timeout' } as const, 'Sign-in timed out'],
		[{ status: 'unsupported_challenge' } as const, 'Unsupported sign-in challenge'],
		[{ status: 'unsafe_challenge_url' } as const, 'Sign-in link refused'],
		['throw' as const, 'Could not complete sign-in'],
	])('discards the new credential when sign-in ends %o', async (outcome, title) => {
		const user = userEvent.setup();
		renderWithProviders(<Host outcome={outcome} />);
		await user.click(screen.getByRole('button', { name: 'created' }));
		expect(await screen.findByText(title)).toBeInTheDocument();
		await waitFor(() => expect(deleted).toEqual(['cred_new']));
		expect(
			await screen.findByText(/The unconnected credential was discarded\./),
		).toBeInTheDocument();
	});

	it.each([
		[{ status: 'cancelled' } as const, 'Sign-in cancelled'],
		[{ status: 'timeout' } as const, 'Sign-in timed out'],
		[{ status: 'unsupported_challenge' } as const, 'Unsupported sign-in challenge'],
		[{ status: 'unsafe_challenge_url' } as const, 'Sign-in link refused'],
		['throw' as const, 'Could not complete sign-in'],
	])(
		'when the discard fails after %o, says the credential is still there (never "discarded")',
		async (outcome, title) => {
			worker.use(
				http.delete('/credentials/:id', ({ params }) => {
					deleted.push(String(params.id));
					return HttpResponse.json({ detail: 'boom' }, { status: 500 });
				}),
			);
			const user = userEvent.setup();
			renderWithProviders(<Host outcome={outcome} />);
			await user.click(screen.getByRole('button', { name: 'created' }));
			expect(
				await screen.findByText(
					'Sign-in didn’t finish and the credential couldn’t be removed — delete it from the Credentials list on the Agents page.',
				),
			).toBeInTheDocument();
			expect(
				screen.getByText(/the credential couldn’t be removed/).closest('[data-variant]'),
			).toHaveAttribute('data-variant', 'error');
			expect(screen.getByText(title)).toBeInTheDocument();
			expect(deleted).toEqual(['cred_new']);
			expect(screen.queryByText(/was discarded/)).not.toBeInTheDocument();
		},
	);

	it('keeps the credential when sign-in redirected (mid-flow in this tab)', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Host outcome={{ status: 'redirected' }} />);
		await user.click(screen.getByRole('button', { name: 'created' }));
		expect(await screen.findByText('Opening sign-in…')).toBeInTheDocument();
		await new Promise((r) => setTimeout(r, 100));
		expect(deleted).toEqual([]);
	});

	it('does nothing for a credential that needs no browser connect', async () => {
		const user = userEvent.setup();
		renderWithProviders(
			<Host
				outcome={{ status: 'cancelled' }}
				info={{ ...OAUTH, type: CredentialType.API_KEY, needsConnect: false }}
			/>,
		);
		await user.click(screen.getByRole('button', { name: 'created' }));
		await new Promise((r) => setTimeout(r, 100));
		expect(screen.queryByText('Opening sign-in…')).not.toBeInTheDocument();
		expect(deleted).toEqual([]);
	});

	it('never discards a listed credential on a standalone connect', async () => {
		const user = userEvent.setup();
		renderWithProviders(<Host outcome={{ status: 'cancelled' }} />);
		await user.click(screen.getByRole('button', { name: 'connect' }));
		expect(await screen.findByText('Connection cancelled')).toBeInTheDocument();
		expect(deleted).toEqual([]);
	});
});
