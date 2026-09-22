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
	listCredentialAgents,
	listCredentials,
	type ListCredentialsParams,
} from './client';
import type { ProviderDiscoveryResponse } from '@/shared/api';
import type {
	AuthCodeChallengeResponse,
	ConnectChallengeResponse,
	ConnectRequestBody,
	CredentialAgentListResponse,
	CredentialCreateRequest,
	CredentialCreateResponse,
	CredentialListResponse,
	CredentialRedactedResponse,
	CredentialUpdateRequest,
	DeviceAuthorizationChallengeResponse,
} from './types';
import { updateCredential } from './client';
import {
	isHttpsVendorUrl,
	openVendorUrl,
	assignVendorUrl,
} from '@/shared/credentials/lib/safe-navigation';

/** Namespaced query keys for the credentials cache slice. */
export const credentialKeys = {
	all: ['credentials'] as const,
	list: (params: ListCredentialsParams = {}) => ['credentials', 'list', params] as const,
	detail: (id: string) => ['credentials', 'detail', id] as const,
	/**
	 * Agents directly bound to one credential (`GET /credentials/{id}/agents`,
	 * theme 5 phase 1's reverse lookup). The agents module's bind / unbind /
	 * resume mutations invalidate this slice (importing this factory — the
	 * sanctioned shared channel) so the credential-side "Bound agents" view
	 * never shows a binding the agent side just changed.
	 */
	agents: (id: string) => ['credentials', 'agents', id] as const,
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

/**
 * Agents directly bound to a credential (`GET /credentials/{id}/agents`) —
 * the read-mostly "Bound agents" section on the credential edit sheet.
 * First page only (default 50): the section is a glanceable summary that
 * links out to each agent's own console for anything deeper. `enabled`
 * gates the read to when the host sheet is actually open.
 */
export function useCredentialAgents(
	id: string | undefined,
	opts: { enabled?: boolean } = {},
): UseQueryResult<CredentialAgentListResponse> {
	return useQuery({
		queryKey: credentialKeys.agents(id ?? '__none__'),
		queryFn: () => listCredentialAgents(id as string),
		enabled: (opts.enabled ?? true) && !!id,
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
	/**
	 * Poll interval while waiting for the callback to land (ms). When
	 * omitted, the device-code branch honours the vendor's requested
	 * cadence (``poll_interval_seconds``, RFC 8628 ``interval``; default
	 * 3s) and the popup branch polls at 1.5s.
	 */
	pollMs?: number;
	/** Give up waiting after this long (ms). */
	timeoutMs?: number;
	/**
	 * Abort the wait loops from the outside (e.g. the device-code
	 * dialog's Cancel button). Aborting resolves the flow with a
	 * ``cancelled`` outcome — no error is thrown.
	 */
	signal?: AbortSignal;
	/**
	 * Render hook invoked when the begin-connect call returns a device_code
	 * challenge (RFC 8628). The caller is responsible for showing the
	 * `user_code` and `verification_uri` to the human; the returned cleanup
	 * (if any) is invoked once the outcome is known so the caller can tear
	 * down the modal. Omit for callers that only support authorization_code
	 * flows — a device_code challenge will surface as `unsupported_challenge`.
	 */
	onDeviceAuthorizationChallenge?: (
		challenge: DeviceAuthorizationChallengeResponse,
	) => (() => void) | void;
}

export type ConnectOutcome =
	| { status: 'connected'; credential: CredentialRedactedResponse }
	| { status: 'redirected' }
	| { status: 'cancelled' }
	| { status: 'timeout' }
	| { status: 'unsupported_challenge' }
	// The vendor's OAuth response carried a non-https URL (e.g. ``javascript:``
	// or ``data:``) — refused before we opened / redirected. See
	// ``lib/safe-navigation.ts`` for the guard rules.
	| { status: 'unsafe_challenge_url' };

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
	const { body, mode = 'popup', pollMs, timeoutMs = 120_000, signal } = options;

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

		// Sleep up to `tickMs`, but resolve early if an advisory message
		// arrived (consuming the signal so the next tick sleeps normally
		// again) or the caller aborted (Cancel button).
		const waitTick = (tickMs: number): Promise<void> =>
			new Promise<void>((resolve) => {
				if (signalled || signal?.aborted) {
					signalled = false;
					resolve();
					return;
				}
				let onAbort: (() => void) | null = null;
				const settle = (): void => {
					clearTimeout(t);
					wake = null;
					if (onAbort) signal?.removeEventListener('abort', onAbort);
					resolve();
				};
				const t = setTimeout(settle, tickMs);
				wake = () => {
					signalled = false;
					settle();
				};
				if (signal) {
					onAbort = settle;
					signal.addEventListener('abort', onAbort, { once: true });
				}
			});

		if (challenge.kind === 'device_authorization') {
			// RFC 8628: no browser redirect. The caller renders `user_code` +
			// `verification_uri`; the ConnectPollScanner drives completion
			// server-side. Callers that don't opt-in to rendering the human
			// step get `unsupported_challenge` back — polling in silence would
			// look like a hang from the user's perspective.
			if (!options.onDeviceAuthorizationChallenge) {
				return { status: 'unsupported_challenge' };
			}
			// Honour the vendor's requested poll cadence (RFC 8628
			// ``interval``) unless the caller pinned one explicitly.
			// RFC 8628 §3.5: when the vendor omits ``interval``, the client
			// MUST use 5 seconds — not our own hunch.
			const deviceTickMs = pollMs ?? (challenge.poll_interval_seconds ?? 5) * 1000;
			const cleanup = options.onDeviceAuthorizationChallenge(challenge);
			try {
				const deadline = Date.now() + timeoutMs;
				while (Date.now() < deadline) {
					await waitTick(deviceTickMs);
					// User cancelled (the device dialog's Cancel button aborts
					// the signal) — stop polling, no error.
					if (signal?.aborted) return { status: 'cancelled' };
					const next = await getCredential(id).catch(() => null);
					if (isConnected(next)) {
						return {
							status: 'connected',
							credential: next as CredentialRedactedResponse,
						};
					}
				}
				return { status: 'timeout' };
			} finally {
				cleanup?.();
			}
		}

		const authCode: AuthCodeChallengeResponse = challenge;

		// The vendor supplied ``authorize_url`` in the challenge JSON; refuse
		// to open or redirect anywhere that isn't https. Any of the three
		// navigation paths below (redirect mode, popup, popup-blocked
		// fallback to full redirect) reaches into the vendor URL, so gate
		// once up front.
		if (!isHttpsVendorUrl(authCode.authorize_url)) {
			return { status: 'unsafe_challenge_url' };
		}

		if (mode === 'redirect') {
			assignVendorUrl(authCode.authorize_url);
			return { status: 'redirected' };
		}

		popup = openVendorUrl(
			authCode.authorize_url,
			'jentic-oauth-connect',
			'popup,width=520,height=720',
		);
		if (!popup) {
			assignVendorUrl(authCode.authorize_url);
			return { status: 'redirected' };
		}
		const activePopup = popup;

		const deadline = Date.now() + timeoutMs;
		const popupTickMs = pollMs ?? 1500;

		while (Date.now() < deadline) {
			await waitTick(popupTickMs);
			if (signal?.aborted) {
				activePopup.close();
				return { status: 'cancelled' };
			}
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

export {
	useAgentsForPicker,
	useConfirmConnectSession,
	useConnectSession,
	usePollConnectSessionStatus,
	useStartAndConfirmVendorConnect,
	useStartIntegrationConnect,
	useVendorAuthCapabilities,
	useVendors,
	type StartAndConfirmResult,
	type StartAndConfirmVars,
} from './vendors-hooks';

export type {
	ConfirmRequest,
	ConfirmResponse,
	ConnectRequest,
	ConnectResponse,
	PermissionRule,
	ReviewScope,
	ReviewSession,
	ScopeClassification,
	SessionStatus,
	StatusResponse,
	VendorAuthCapabilities,
	VendorFlow,
	VendorListResponse,
	VendorScopeCatalog,
	VendorSummary,
} from './vendors-types';

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
