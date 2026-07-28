import {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useMemo,
	useRef,
	useSyncExternalStore,
} from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
	UsersService,
	clearToken,
	getSessionExpiresAt,
	getToken,
	HEALTH_QUERY_KEY,
	isAuthError,
	isClientError,
	setSession,
	subscribeToken,
	type CreateAdminRequest,
	type CurrentUserResponse,
	type LoginRequest,
	type LoginResponse,
} from '@/shared/api';

export type AuthStatus = 'loading' | 'authenticated' | 'unauthenticated';

export interface AuthContextValue {
	status: AuthStatus;
	user: CurrentUserResponse | null;
	/** True once logged in but the backend requires a password change first. */
	mustChangePassword: boolean;
	login: (credentials: LoginRequest) => Promise<void>;
	/** First-run setup: create the first admin and adopt the returned session. */
	createAdmin: (payload: CreateAdminRequest) => Promise<void>;
	/**
	 * Rotate the current password. The backend returns a fresh token (with the
	 * must_change_password gate cleared); we adopt it so the caller need not
	 * re-login. Returns nothing — the new session is live on resolve.
	 */
	changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
	/**
	 * Redeem a user invite: set the initial password for an invited account and
	 * adopt the returned session (auto-login), so the invitee lands in the app
	 * without a second round-trip. Unauthenticated entry point (the invitee has
	 * no session yet) — mirrors `createAdmin`/`login`'s token adoption.
	 */
	redeemInvite: (inviteToken: string, password: string) => Promise<void>;
	/** Client-side sign-out — jentic-one JWTs are stateless (no revoke endpoint). */
	logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const ME_QUERY_KEY = ['auth', 'me'] as const;

/** Subscribe to the token store so the provider re-renders on login/logout. */
function useTokenValue(): string | null {
	return useSyncExternalStore(subscribeToken, getToken, getToken);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
	const token = useTokenValue();
	const queryClient = useQueryClient();

	const meQuery = useQuery({
		queryKey: ME_QUERY_KEY,
		queryFn: () => UsersService.getCurrentUser(),
		// Only fetch the profile when we actually hold a token.
		enabled: token !== null,
		staleTime: 60_000,
	});

	// A token that the backend rejects (401/403) is dead — drop it so the UI
	// falls back to the login screen instead of looping on a doomed request.
	// Only act once the query is settled (`fetchStatus === 'idle'`): while a
	// refetch is in flight React Query still exposes the *previous* error, so
	// without the guard a cached 401 from an expired token would clear the
	// freshly-minted token adopted right before the refetch (#610).
	useEffect(() => {
		if (
			token !== null &&
			meQuery.isError &&
			meQuery.fetchStatus === 'idle' &&
			isAuthError(meQuery.error)
		) {
			// Record why the token vanished so the login page can say "session
			// expired" instead of silently presenting a bare form (#608).
			clearToken('session-expired');
		}
	}, [token, meQuery.isError, meQuery.fetchStatus, meQuery.error]);

	// Adopt a freshly-minted session bundle. The previous token's /users/me
	// state is removed *before* the new token lands so no stale 401 can be
	// (mis)attributed to it — the root cause of the login-needs-retries bug
	// (#610) — then a fresh fetch resolves the new identity before we return.
	const adoptSession = useCallback(
		async (bundle: LoginResponse) => {
			queryClient.removeQueries({ queryKey: ME_QUERY_KEY });
			setSession(bundle.access_token, bundle.expires_in);
			await queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
		},
		[queryClient],
	);

	// Proactive sliding-session renewal (#608): re-mint the JWT at ~80% of its
	// remaining lifetime so an active operator never hits the hard 1-hour
	// expiry. Refresh failure falls through to the normal expiry path (the next
	// 401 clears the token with a session-expired notice). Background tabs may
	// throttle the timer; the visibility listener catches up on return.
	const refreshInFlight = useRef(false);
	useEffect(() => {
		if (token === null) return;
		const expiresAt = getSessionExpiresAt();
		if (expiresAt === null) return; // Legacy token without expiry metadata.

		let timer: ReturnType<typeof setTimeout> | undefined;
		let disposed = false;
		const refreshDueAt = Date.now() + (expiresAt - Date.now()) * 0.8;

		const runRefresh = async () => {
			if (disposed || refreshInFlight.current) return;
			refreshInFlight.current = true;
			try {
				const bundle = await UsersService.refreshSession();
				// The effect is torn down (disposed) on any token change — logout or
				// a different session being adopted while the request was in flight.
				// A late response must not resurrect the old session over it.
				if (disposed) return;
				// setSession notifies the token store, re-running this effect with
				// the new expiry — the next cycle schedules itself.
				setSession(bundle.access_token, bundle.expires_in);
			} catch (error) {
				if (disposed) return; // Same guard: never clobber a successor session.
				if (isAuthError(error)) {
					// Refresh refused (absolute window exceeded, user deactivated…):
					// the session is over — surface the login page with the notice.
					clearToken('session-expired');
				} else if (!isClientError(error) && Date.now() < expiresAt) {
					// Transient failure (network blip, 5xx): retry while the token
					// lives. Other 4xx (e.g. 404 from a backend without the refresh
					// endpoint) are deterministic — don't loop; the session simply
					// ends at its natural expiry.
					timer = setTimeout(() => void runRefresh(), 30_000);
				}
			} finally {
				refreshInFlight.current = false;
			}
		};

		const onVisible = () => {
			// Timers are throttled in hidden tabs — if the refresh slot passed
			// while hidden, catch up as soon as the tab is visible again.
			if (document.visibilityState === 'visible' && Date.now() >= refreshDueAt) {
				void runRefresh();
			}
		};

		timer = setTimeout(() => void runRefresh(), Math.max(refreshDueAt - Date.now(), 0));
		document.addEventListener('visibilitychange', onVisible);
		return () => {
			disposed = true;
			if (timer !== undefined) clearTimeout(timer);
			document.removeEventListener('visibilitychange', onVisible);
		};
	}, [token]);

	const login = useCallback(
		async (credentials: LoginRequest) => {
			const result = await UsersService.login({ requestBody: credentials });
			// Force a fresh /users/me so the new identity is reflected immediately.
			await adoptSession(result);
		},
		[adoptSession],
	);

	const createAdmin = useCallback(
		async (payload: CreateAdminRequest) => {
			// First-run setup returns a ready-to-use token (auto-login), so the
			// operator lands authenticated without a second round-trip.
			const result = await UsersService.createAdmin({ requestBody: payload });
			await adoptSession(result);
			// Setup just closed: mark the cached health/setup flag stale so any
			// later pass through the SetupGate (back-nav, logout→login) refetches
			// setup_required:false instead of bouncing the new admin to /setup.
			// Do NOT await/refetch here — the caller navigates to /app (outside the
			// SetupGate) immediately, and forcing a synchronous refetch would let
			// the still-mounted gate observe setup_required:false and redirect to
			// /login, racing that navigation. invalidateQueries with refetchType
			// 'none' marks stale without an in-flight refetch.
			void queryClient.invalidateQueries({
				queryKey: HEALTH_QUERY_KEY,
				refetchType: 'none',
			});
		},
		[adoptSession, queryClient],
	);

	const changePassword = useCallback(
		async (currentPassword: string, newPassword: string) => {
			// The endpoint re-mints the token with must_change_password cleared;
			// adopt it so the stale gate claim can't loop the AuthGuard.
			const result = await UsersService.changePassword({
				requestBody: { current_password: currentPassword, new_password: newPassword },
			});
			await adoptSession(result);
		},
		[adoptSession],
	);

	const redeemInvite = useCallback(
		async (inviteToken: string, password: string) => {
			// Redemption returns a ready-to-use token (auto-login), same shape as
			// login/createAdmin — adopt it and refresh /users/me.
			const result = await UsersService.redeemInvite({
				requestBody: { invite_token: inviteToken, password },
			});
			await adoptSession(result);
		},
		[adoptSession],
	);

	const logout = useCallback(() => {
		clearToken();
		queryClient.removeQueries({ queryKey: ME_QUERY_KEY });
		queryClient.clear();
	}, [queryClient]);

	const value = useMemo<AuthContextValue>(() => {
		let status: AuthStatus;
		if (token === null) {
			status = 'unauthenticated';
		} else if (meQuery.isSuccess) {
			status = 'authenticated';
		} else if (meQuery.isError) {
			status = 'unauthenticated';
		} else {
			status = 'loading';
		}
		const user = meQuery.data ?? null;
		return {
			status,
			user,
			mustChangePassword: user?.must_change_password ?? false,
			login,
			createAdmin,
			changePassword,
			redeemInvite,
			logout,
		};
	}, [
		token,
		meQuery.isSuccess,
		meQuery.isError,
		meQuery.data,
		login,
		createAdmin,
		changePassword,
		redeemInvite,
		logout,
	]);

	return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
	const ctx = useContext(AuthContext);
	if (ctx === null) {
		throw new Error('useAuth must be used within an AuthProvider');
	}
	return ctx;
}
