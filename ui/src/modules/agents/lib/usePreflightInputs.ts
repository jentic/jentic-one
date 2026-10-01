/**
 * What `preflightApis` needs besides the picks, read the same way by every
 * surface that preflights (the Add-APIs tray, and the page's auto-queue for
 * APIs chosen before the tray, like the landing's GitHub).
 *
 * The credential list is DRAINED: a first-page-only list would call an existing
 * credential "needs a new credential" and hide it from the choice.
 */
import { useMemo } from 'react';
import { useAllCredentials, useProviders } from '@/shared/credentials/api';
import { useOptionalCurrentUser } from '@/shared/auth';
import type { CredentialBindingEntity } from '@/modules/agents/api/types';
import type { PreflightInputs } from '@/modules/agents/lib/apiPreflight';

const NO_BINDINGS: CredentialBindingEntity[] = [];

export function usePreflightInputs(bindings: CredentialBindingEntity[] | undefined) {
	const credentialsSource = useAllCredentials();
	// Narrows the choice to credentials this user may bind; unknown → no filter.
	const viewer = useOptionalCurrentUser();
	const providersQuery = useProviders();
	const managedOAuthAvailable = useMemo(
		() => (providersQuery.data?.providers ?? []).some((p) => p.managed && p.configured),
		[providersQuery.data],
	);
	const inputs = useMemo<PreflightInputs>(
		() => ({
			credentials: credentialsSource.items,
			viewer,
			bindings: bindings ?? NO_BINDINGS,
			managedOAuthAvailable,
		}),
		[credentialsSource.items, viewer, bindings, managedOAuthAvailable],
	);
	return {
		inputs,
		credentialsSource,
		/** Every input is read: the bindings, the whole credential list and the
		 * providers (a failed providers read counts as "no managed OAuth"). */
		ready: bindings !== undefined && credentialsSource.complete && !providersQuery.isPending,
	};
}
