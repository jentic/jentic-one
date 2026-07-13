import {
	createContext,
	useCallback,
	useContext,
	useEffect,
	useMemo,
	useSyncExternalStore,
} from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
	UsersService,
	clearToken,
	getToken,
	HEALTH_QUERY_KEY,
	isAuthError,
	setToken,
	subscribeToken,
	type CreateAdminRequest,
	type CurrentUserResponse,
	type LoginRequest,
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
	useEffect(() => {
		if (token !== null && meQuery.isError && isAuthError(meQuery.error)) {
			clearToken();
		}
	}, [token, meQuery.isError, meQuery.error]);

	const login = useCallback(
		async (credentials: LoginRequest) => {
			const result = await UsersService.login({ requestBody: credentials });
			setToken(result.access_token);
			// Force a fresh /users/me so the new identity is reflected immediately.
			await queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
		},
		[queryClient],
	);

	const createAdmin = useCallback(
		async (payload: CreateAdminRequest) => {
			// First-run setup returns a ready-to-use token (auto-login), so the
			// operator lands authenticated without a second round-trip.
			const result = await UsersService.createAdmin({ requestBody: payload });
			setToken(result.access_token);
			await queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
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
		[queryClient],
	);

	const changePassword = useCallback(
		async (currentPassword: string, newPassword: string) => {
			// The endpoint re-mints the token with must_change_password cleared;
			// adopt it so the stale gate claim can't loop the AuthGuard.
			const result = await UsersService.changePassword({
				requestBody: { current_password: currentPassword, new_password: newPassword },
			});
			setToken(result.access_token);
			await queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
		},
		[queryClient],
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
