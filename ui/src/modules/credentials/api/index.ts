// Credentials hooks layer (≈ backend service). React Query hooks that own this
// module's cache slice (query keys namespaced under `['credentials', …]`),
// pagination policy, and invalidation. Components/pages call these hooks ONLY —
// never the data client, the facade, or the generated services directly.
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import {
	connectCredential,
	createCredential,
	deleteCredential,
	getCredential,
	getProviders,
	listCredentials,
	type ListCredentialsParams,
} from './client';
import type { ProviderDiscoveryResponse } from '@/shared/api';
import type {
	ConnectChallengeResponse,
	ConnectRequestBody,
	CredentialCreateRequest,
	CredentialCreateResponse,
	CredentialListResponse,
	CredentialRedactedResponse,
	CredentialUpdateRequest,
} from './types';
import { updateCredential } from './client';

/** Namespaced query keys for the credentials cache slice. */
export const credentialKeys = {
	all: ['credentials'] as const,
	list: (params: ListCredentialsParams = {}) => ['credentials', 'list', params] as const,
	detail: (id: string) => ['credentials', 'detail', id] as const,
};

/**
 * Wire contract for the advisory popup→opener connect signal (#598).
 *
 * The connect flow lives in this module, so the protocol constant is owned
 * here. The shared `/oauth/connected` page (`OAuthPopupReturn`) is the popup
 * side that posts the message; it can't import this module (shared never
 * imports modules/), so it carries a private copy of the same string literal
 * pinned equal by a regression test. The message is *advisory only* — it
 * carries no credential id, token, or reason, and the opener always re-reads
 * the credentials API to learn the authoritative outcome.
 */
export const OAUTH_CONNECT_MESSAGE_TYPE = 'jentic:oauth-connect' as const;

export interface OAuthConnectMessage {
	type: typeof OAUTH_CONNECT_MESSAGE_TYPE;
	status: 'ok' | 'error';
}

/** List credentials (first page; cursor pagination policy owned here). */
export function useCredentials(
	params: ListCredentialsParams = {},
): UseQueryResult<CredentialListResponse> {
	return useQuery({
		queryKey: credentialKeys.list(params),
		queryFn: () => listCredentials(params),
	});
}

/** A single credential's redacted detail. */
export function useCredential(id: string | undefined): UseQueryResult<CredentialRedactedResponse> {
	return useQuery({
		queryKey: credentialKeys.detail(id ?? '__none__'),
		queryFn: () => getCredential(id as string),
		enabled: !!id,
	});
}

/** Create a credential. The one-time `secret` is on the resolved value. */
export function useCreateCredential() {
	const queryClient = useQueryClient();
	return useMutation<CredentialCreateResponse, Error, CredentialCreateRequest>({
		mutationFn: (body) => createCredential(body),
		onSuccess: () => {
			void queryClient.invalidateQueries({ queryKey: credentialKeys.all });
		},
	});
}

/** Update / rotate a credential. */
export function useUpdateCredential(id: string) {
	const queryClient = useQueryClient();
	return useMutation<CredentialRedactedResponse, Error, CredentialUpdateRequest>({
		mutationFn: (body) => updateCredential(id, body),
		onSuccess: () => {
			void queryClient.invalidateQueries({ queryKey: credentialKeys.all });
			void queryClient.invalidateQueries({ queryKey: credentialKeys.detail(id) });
		},
	});
}

/** Delete a credential. */
export function useDeleteCredential() {
	const queryClient = useQueryClient();
	return useMutation<void, Error, string>({
		mutationFn: (id) => deleteCredential(id),
		onSuccess: () => {
			void queryClient.invalidateQueries({ queryKey: credentialKeys.all });
		},
	});
}

/** Begin the OAuth connect flow for a credential. */
export function useConnectCredential(id: string) {
	return useMutation<ConnectChallengeResponse, Error, ConnectRequestBody | void>({
		mutationFn: (body) => connectCredential(id, body ?? undefined),
	});
}

/** Fetch provider discovery metadata (types, managed flag, callback URLs). */
export function useProviders(): UseQueryResult<ProviderDiscoveryResponse> {
	return useQuery({
		queryKey: [...credentialKeys.all, 'providers'] as const,
		queryFn: () => getProviders(),
		staleTime: 5 * 60 * 1000,
	});
}

export interface RunConnectOptions {
	/** Optional scopes/extra forwarded to the begin-connect call. */
	body?: ConnectRequestBody;
	/** Open the authorize URL in a popup (default) or the same tab. */
	mode?: 'popup' | 'redirect';
	/** Poll interval while waiting for the callback to land (ms). */
	pollMs?: number;
	/** Give up waiting after this long (ms). */
	timeoutMs?: number;
}

export type ConnectOutcome =
	| { status: 'connected'; credential: CredentialRedactedResponse }
	| { status: 'redirected' }
	| { status: 'cancelled' }
	| { status: 'timeout' };

/**
 * Run the full OAuth connect round-trip for a credential.
 *
 * The backend callback (`GET /credentials/oauth/callback`) persists the
 * connection server-side, then 303-redirects the popup to the public
 * `/oauth/connected` route (which self-closes) rather than signalling the SPA
 * directly. So the robust signal is to (1) begin the flow, (2) open the
 * provider/Pipedream authorize URL in a popup, then (3) wait for the credential
 * to report a connection (a `provider_account_ref` appears, or the credential
 * otherwise updates) or the popup to close.
 *
 * The wait is poll-based, but the `/oauth/connected` page also posts an
 * advisory `postMessage` to this opener (#598). When that arrives we re-read
 * the credential *immediately* instead of waiting for the next poll tick — the
 * message is only a "check now" nudge; the credentials API stays authoritative
 * (we never trust the message payload as the outcome). Polling remains the
 * fallback for blocked/cross-origin/lost-opener cases.
 *
 * Falls back to a same-tab redirect when popups are blocked or `mode:'redirect'`
 * is requested — in that case the caller can't observe completion, so the
 * outcome is `redirected`.
 */
export async function runConnectFlow(
	id: string,
	options: RunConnectOptions = {},
): Promise<ConnectOutcome> {
	const { body, mode = 'popup', pollMs = 1500, timeoutMs = 120_000 } = options;

	// Advisory wake-up plumbing (#598). We attach the listener *before* the
	// connect round-trip so a popup that completes very fast (cached IdP consent)
	// can't post its message into a window where no listener is yet attached. The
	// flag is consumed once per tick (not a permanent latch): a single message
	// wakes the loop exactly once — after the re-read, pacing resumes at `pollMs`.
	// Otherwise a `status: 'error'` message (which never flips the credential to
	// connected) would turn the loop into a tight request storm until the
	// deadline. The message is never treated as the source of truth — the opener
	// always re-reads `GET /credentials/{id}` for the authoritative outcome.
	let signalled = false;
	let wake: (() => void) | null = null;
	let popup: Window | null = null;
	const onMessage = (event: MessageEvent): void => {
		if (event.origin !== window.location.origin) return;
		// Only honour the nudge from the window we actually opened (defence in
		// depth — an unrelated same-origin tab/iframe shouldn't pace our loop).
		if (popup && event.source && event.source !== popup) return;
		const data = event.data as Partial<OAuthConnectMessage> | null;
		if (data?.type !== OAUTH_CONNECT_MESSAGE_TYPE) return;
		signalled = true;
		wake?.();
	};
	window.addEventListener('message', onMessage);

	try {
		const challenge = await connectCredential(id, body ?? undefined);

		const before = await getCredential(id).catch(() => null);
		// If the baseline read failed we have no reference point, so we can't tell
		// a *fresh* connection from a credential that was already connected. Don't
		// let a transient baseline failure make the first tick a false positive —
		// require a real baseline before trusting the `updated_at`/ref deltas.
		const haveBaseline = before !== null;

		if (mode === 'redirect') {
			window.location.assign(challenge.authorize_url);
			return { status: 'redirected' };
		}

		popup = window.open(
			challenge.authorize_url,
			'jentic-oauth-connect',
			'popup,width=520,height=720',
		);
		if (!popup) {
			window.location.assign(challenge.authorize_url);
			return { status: 'redirected' };
		}
		const activePopup = popup;

		const deadline = Date.now() + timeoutMs;
		const isConnected = (next: CredentialRedactedResponse | null): boolean => {
			if (!next) return false;
			if (!haveBaseline) return false;
			if (
				next.provider_account_ref &&
				next.provider_account_ref !== before?.provider_account_ref
			) {
				return true;
			}
			// Direct OAuth2 stores tokens without a provider_account_ref; fall back to
			// an updated_at bump as the "something changed" signal.
			return !!next.updated_at && next.updated_at !== before?.updated_at;
		};

		// Sleep up to `pollMs`, but resolve early if an advisory message arrived
		// (consuming the signal so the next tick sleeps normally again).
		const waitTick = (): Promise<void> =>
			new Promise<void>((resolve) => {
				if (signalled) {
					signalled = false;
					resolve();
					return;
				}
				const t = setTimeout(() => {
					wake = null;
					resolve();
				}, pollMs);
				wake = () => {
					clearTimeout(t);
					wake = null;
					signalled = false;
					resolve();
				};
			});

		while (Date.now() < deadline) {
			await waitTick();
			const next = await getCredential(id).catch(() => null);
			if (isConnected(next)) {
				activePopup.close();
				return { status: 'connected', credential: next as CredentialRedactedResponse };
			}
			if (activePopup.closed) {
				// The user closed the popup. Do one last read in case the callback
				// landed right before they closed it.
				const last = await getCredential(id).catch(() => null);
				if (isConnected(last)) {
					return {
						status: 'connected',
						credential: last as CredentialRedactedResponse,
					};
				}
				return { status: 'cancelled' };
			}
		}

		activePopup.close();
		return { status: 'timeout' };
	} finally {
		window.removeEventListener('message', onMessage);
	}
}

export type { ListCredentialsParams } from './client';
export * from './types';

export {
	apiPickerKeys,
	useApis,
	useApiSchemes,
	useCatalog,
	useImportCatalogEntry,
	type SelectedApi,
	type ServerVarDef,
} from './apis-hooks';

// Re-export the API/catalog response models so view code can stay within the
// module boundary (the lint rule blocks direct `@/shared/api` imports).
export type {
	ApiResponse,
	ApiListResponse,
	CatalogEntryResponse,
	CatalogListResponse,
	ProviderDiscoveryResponse,
	ProviderDiscoveryEntryResponse,
} from '@/shared/api';
