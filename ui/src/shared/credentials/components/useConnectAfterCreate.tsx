import { useCallback, type ReactNode } from 'react';
import { toast } from '@/shared/ui';
import { CredentialType, useDeleteCredential, type ConnectOutcome } from '@/shared/credentials/api';
import { useDeviceAwareConnect } from '@/shared/credentials/components/useDeviceAwareConnect';
import type { CreatedCredentialInfo } from '@/shared/credentials/components/CreateCredentialFlow';

type Connect = ReturnType<typeof useDeviceAwareConnect>['connect'];

/**
 * The OAuth connect every credential surface runs (the agents inventory
 * sheet and API-access sidebar, the API hub), with the
 * device-code step wired in (`useDeviceAwareConnect`). Render `deviceDialog`
 * once in the host.
 *
 *   - `afterCreate` — what a `CreateCredentialFlow` host does once the
 *     credential is saved: an OAuth2 credential with a user-interactive grant
 *     opens its sign-in straight away; anything else is ready as is. A
 *     freshly-created credential that never completes its sign-in is unusable
 *     ("if it wasn't signed, we shouldn't store it"), so an abandoned, timed-out
 *     or failed handshake DISCARDS it. `redirected` (popup blocked → same-tab
 *     navigation) must NOT clean up: the user is mid-flow and the callback
 *     lands on return.
 *   - `connectExisting` — the standalone Connect action on a listed
 *     credential: keeps the credential whatever the outcome.
 *
 * `connect` may be injected (tests, or a host that already owns a device-aware
 * connect); `deviceDialog` is then null — the injector renders its own.
 */
export function useConnectAfterCreate(opts: { connect?: Connect } = {}): {
	afterCreate: (info: CreatedCredentialInfo) => void;
	connectExisting: (credentialId: string, credentialName: string) => Promise<void>;
	deviceDialog: ReactNode;
} {
	const own = useDeviceAwareConnect();
	const connect = opts.connect ?? own.connect;
	const { mutateAsync: deleteCredential } = useDeleteCredential();

	const afterCreate = useCallback(
		(info: CreatedCredentialInfo): void => {
			if (
				info.type !== CredentialType.OAUTH2 ||
				info.provider === 'static' ||
				!info.needsConnect
			) {
				return;
			}
			const discard = async (): Promise<void> => {
				try {
					await deleteCredential(info.credentialId);
				} catch {
					// Best-effort cleanup; the row stays listed if the delete fails.
				}
			};
			const discarded = (title: string, variant?: 'error'): void => {
				toast({
					title,
					description: 'The unconnected credential was discarded.',
					variant,
				});
			};
			void (async (): Promise<void> => {
				toast({ title: 'Opening sign-in…' });
				try {
					const outcome = await connect(info.credentialId, info.name);
					switch (outcome.status) {
						case 'connected':
							toast({ title: 'Connected', variant: 'success' });
							break;
						case 'redirected':
							break;
						case 'cancelled':
							await discard();
							discarded('Sign-in cancelled');
							break;
						case 'timeout':
							await discard();
							toast({
								title: 'Sign-in timed out',
								description: 'The unconnected credential was discarded. Try again.',
								variant: 'error',
							});
							break;
						case 'unsupported_challenge':
							await discard();
							discarded('Unsupported sign-in challenge', 'error');
							break;
						case 'unsafe_challenge_url':
							await discard();
							toast({
								title: 'Sign-in link refused',
								description:
									'The provider returned an unsafe sign-in URL. The unconnected credential was discarded.',
								variant: 'error',
							});
							break;
					}
				} catch {
					await discard();
					discarded('Could not complete sign-in', 'error');
				}
			})();
		},
		[connect, deleteCredential],
	);

	const connectExisting = useCallback(
		async (credentialId: string, credentialName: string): Promise<void> => {
			toast({ title: `Opening sign-in for ${credentialName}…` });
			let outcome: ConnectOutcome;
			try {
				outcome = await connect(credentialId, credentialName);
			} catch {
				toast({ title: 'Could not start the OAuth flow', variant: 'error' });
				return;
			}
			switch (outcome.status) {
				case 'connected':
					toast({ title: 'Connected', variant: 'success' });
					break;
				case 'redirected':
					break;
				case 'cancelled':
					toast({ title: 'Connection cancelled' });
					break;
				case 'timeout':
					toast({
						title: 'Connection timed out',
						description: 'Finish the sign-in and refresh to see the result.',
						variant: 'error',
					});
					break;
				case 'unsupported_challenge':
					toast({ title: 'Unsupported sign-in challenge', variant: 'error' });
					break;
				case 'unsafe_challenge_url':
					toast({
						title: 'Sign-in link refused',
						description: 'The provider returned an unsafe sign-in URL.',
						variant: 'error',
					});
					break;
			}
		},
		[connect],
	);

	return {
		afterCreate,
		connectExisting,
		deviceDialog: opts.connect ? null : own.deviceDialog,
	};
}
