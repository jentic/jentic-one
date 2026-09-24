import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { useAuth } from '@/shared/auth/AuthContext';
import { consumeCodeVerifier, ssoRedirectUri, SPA_CLIENT_ID } from '@/shared/auth/sso';
import { exchangeAuthCode } from '@/shared/api';
import { ROUTES } from '@/shared/app/routes';
import { AppLink } from '@/shared/ui/AppLink';
import { ErrorAlert } from '@/shared/ui/ErrorAlert';

/**
 * SSO authorization-code callback landing.
 *
 * The backend redirects the browser here (→ `/app/auth/callback`) after the IdP
 * round-trip, with a one-time platform `code`. This page exchanges that code +
 * the stored PKCE verifier for a session (`POST /oauth/token`), adopts it via
 * the shared auth context, and sends the operator into the app.
 *
 * Lives outside the AuthGuard — the user has no session yet. On any failure it
 * shows a concise error with a link back to the login form (never a dead end).
 */
export function SsoCallbackPage() {
	const [params] = useSearchParams();
	const { loginWithSession } = useAuth();
	const navigate = useNavigate();
	const [error, setError] = useState<string | null>(null);
	// Guard against React StrictMode double-invocation: the platform code is
	// single-use, so a second exchange would fail. Run the exchange at most once.
	const startedRef = useRef(false);

	useEffect(() => {
		if (startedRef.current) return;
		startedRef.current = true;

		const code = params.get('code');
		const verifier = consumeCodeVerifier();

		if (!code || !verifier) {
			setError('Sign-in link is invalid or has expired. Please try again.');
			return;
		}

		void (async () => {
			try {
				const bundle = await exchangeAuthCode({
					code,
					codeVerifier: verifier,
					redirectUri: ssoRedirectUri(),
					clientId: SPA_CLIENT_ID,
				});
				await loginWithSession(bundle);
				navigate(ROUTES.app, { replace: true });
			} catch {
				setError('Sign-in failed. Please try again.');
			}
		})();
	}, [params, loginWithSession, navigate]);

	return (
		<main className="bg-background text-foreground flex min-h-screen items-center justify-center px-4">
			<div
				className="border-border bg-card w-full max-w-sm rounded-xl border p-6 text-center shadow-sm"
				role={error ? undefined : 'status'}
			>
				{error === null ? (
					<>
						<h1 className="font-display text-lg font-semibold">Signing you in…</h1>
						<p className="text-muted-foreground mt-2 text-sm">
							Completing sign-in, one moment.
						</p>
					</>
				) : (
					<>
						<h1 className="font-display text-lg font-semibold">Sign-in failed</h1>
						<ErrorAlert message={error} className="mt-4" />
						<AppLink
							href={ROUTES.login}
							className="text-muted-foreground hover:text-foreground mt-5 inline-block text-sm underline-offset-2 hover:underline"
						>
							Back to sign in
						</AppLink>
					</>
				)}
			</div>
		</main>
	);
}
