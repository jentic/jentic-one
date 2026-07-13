// Credential-provider presentation config.
//
// The backend exposes `GET /credentials/providers` which returns full discovery
// metadata (callback URLs, managed flag, supported types, and a `configured`
// flag). The provider *option list* below is derived from that discovery
// result: the managed Pipedream option appears only when the backend reports a
// `pipedream` provider with `configured === true`. There is no build-time
// feature flag — runtime config (set via `admin config providers`) decides.
import type { ProviderDiscoveryEntryResponse } from '@/shared/api';
import { CredentialType } from '@/modules/credentials/api';

/** The credential provider the platform uses to gather/refresh an OAuth2 grant. */
export type CredentialProviderId = 'static' | 'direct_oauth2' | 'pipedream';

export interface ProviderOption {
	id: CredentialProviderId;
	label: string;
	/** One-line explanation shown under the provider picker. */
	description: string;
	/**
	 * Managed providers (e.g. Pipedream) own the vendor OAuth client, so the
	 * platform stores only an account reference — no vendor refresh token. The
	 * credential is "used" via the Connect flow, not by us minting tokens.
	 */
	managed: boolean;
}

const STATIC_OPTION: ProviderOption = {
	id: 'static',
	label: 'Manual token entry (no connect flow)',
	description:
		'You paste an existing access/refresh token directly. No browser redirect or code exchange takes place.',
	managed: false,
};

const DIRECT_OAUTH2_OPTION: ProviderOption = {
	id: 'direct_oauth2',
	label: 'OAuth2 Connect (authorization code exchange)',
	description:
		'The platform manages the full OAuth2 authorization code flow. You supply client credentials and URLs; we handle the browser redirect and token lifecycle.',
	managed: false,
};

const PIPEDREAM_OPTION: ProviderOption = {
	id: 'pipedream',
	label: 'Pipedream (managed OAuth)',
	description:
		'Pipedream holds the vendor relationship and refresh token. Connect once via the Pipedream-hosted link; we store only an account reference.',
	managed: true,
};

/**
 * Provider options available for a given credential type. Only `oauth2` has a
 * meaningful provider choice; the other types are always directly stored
 * ("static"), so they get no picker.
 *
 * The managed Pipedream option is included only when `discovered` contains a
 * `pipedream` entry reported as `configured` by the backend — i.e. an operator
 * has set it up at runtime (`admin config providers set pipedream …`). Pass the
 * `providers` array from `useProviders()`; omit it (or pass `[]`) to get just
 * the always-available direct options.
 *
 * NOTE: `direct_oauth2` is listed first (and thus the default) because the
 * platform-managed authorization code flow is the primary path for OAuth2
 * credentials today. This ordering will likely change once we support
 * additional managed providers or user-selectable broker strategies.
 */
export function providerOptions(
	type: CredentialType,
	discovered: ProviderDiscoveryEntryResponse[] = [],
): ProviderOption[] {
	if (type !== CredentialType.OAUTH2) return [STATIC_OPTION];
	const options: ProviderOption[] = [DIRECT_OAUTH2_OPTION, STATIC_OPTION];
	const pipedreamReady = discovered.some((p) => p.id === 'pipedream' && p.configured);
	if (pipedreamReady) options.push(PIPEDREAM_OPTION);
	return options;
}

/** True when the credential's stored provider is a managed (Pipedream) one. */
export function isManagedProvider(provider: string | null | undefined): boolean {
	return provider === 'pipedream';
}

/**
 * Friendly message for the most likely failure when a managed provider is
 * selected but the backend has no such provider configured.
 *
 * The control API does not advertise its configured providers (see the note at
 * the top of this file / jentic-one#388), and an unconfigured `provider` lookup
 * currently fails server-side as a 5xx at create time rather than a clean 4xx.
 * We translate that into actionable guidance instead of a raw "Internal Server
 * Error" when the selected provider is managed.
 */
export function managedProviderUnavailableMessage(provider: string, err: unknown): string | null {
	if (!isManagedProvider(provider)) return null;
	const status = (err as { status?: number } | null)?.status;
	// Treat any server-side failure (or an explicit unknown-provider 4xx) as the
	// provider-not-enabled case, since that's the dominant cause here.
	if (status == null || status >= 500 || status === 400 || status === 422) {
		return "Pipedream isn't enabled on this server. The 'Pipedream (managed)' option requires the backend to be configured with a Pipedream provider (project + client). Use a direct provider, or ask an operator to enable Pipedream.";
	}
	return null;
}
