import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { useAuth } from '@/shared/auth/AuthContext';
import { ApiError, consumeSessionExpiredNotice } from '@/shared/api';
import { ROUTES } from '@/shared/app/routes';
import { Input } from '@/shared/ui/Input';
import { Label } from '@/shared/ui/Label';
import { Button } from '@/shared/ui/Button';
import { ErrorAlert } from '@/shared/ui/ErrorAlert';

interface LocationState {
	from?: { pathname?: string };
}

/**
 * Login screen. Lives outside the authenticated Layout. On success the auth
 * context resolves the session and we send the user to where they were headed
 * (or the app home). The change-password gate is enforced separately by
 * AuthGuard once must_change_password is known.
 */
export function LoginPage() {
	const { login } = useAuth();
	const navigate = useNavigate();
	const location = useLocation();
	const [email, setEmail] = useState('');
	const [password, setPassword] = useState('');
	const [error, setError] = useState<string | null>(null);
	const [submitting, setSubmitting] = useState(false);
	// One-shot notice recorded when a rejected/expired token was cleared —
	// tells the user *why* they're back on the login form (#608). Consumed in
	// an effect (not a lazy initializer): consuming mutates sessionStorage, and
	// StrictMode's double-invoked initializer would eat the flag before the
	// surviving render sees it. The effect only ever raises the state, so the
	// StrictMode re-mount (flag already consumed) leaves it intact.
	const [sessionExpired, setSessionExpired] = useState(false);
	useEffect(() => {
		if (consumeSessionExpiredNotice()) setSessionExpired(true);
	}, []);

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault();
		setError(null);
		setSessionExpired(false);
		setSubmitting(true);
		try {
			await login({ email, password });
			const from = (location.state as LocationState | null)?.from?.pathname;
			navigate(from && from !== ROUTES.login ? from : ROUTES.app, { replace: true });
		} catch (err) {
			if (err instanceof ApiError && err.status === 401) {
				setError('Incorrect email or password.');
			} else {
				setError('Sign-in failed. Please try again.');
			}
		} finally {
			setSubmitting(false);
		}
	};

	return (
		<main className="bg-background text-foreground flex min-h-screen items-center justify-center px-4">
			<form
				onSubmit={handleSubmit}
				className="border-border bg-card w-full max-w-sm rounded-xl border p-6 shadow-sm"
				aria-labelledby="login-heading"
			>
				<h1 id="login-heading" className="font-display text-xl font-semibold">
					Sign in to Jentic One
				</h1>
				<p className="text-muted-foreground mt-1 text-sm">Admin console</p>

				{sessionExpired && (
					<p
						role="status"
						className="border-border bg-muted/60 text-muted-foreground mt-4 rounded-lg border px-4 py-3 text-sm"
					>
						Your session expired — please sign in again.
					</p>
				)}

				<div className="mt-6 space-y-4">
					<div className="space-y-1">
						<Label htmlFor="login-email">Email</Label>
						<Input
							id="login-email"
							type="email"
							autoComplete="username"
							required
							value={email}
							onChange={(e) => setEmail(e.target.value)}
						/>
					</div>
					<div className="space-y-1">
						<Label htmlFor="login-password">Password</Label>
						<Input
							id="login-password"
							type="password"
							autoComplete="current-password"
							showPasswordToggle
							required
							value={password}
							onChange={(e) => setPassword(e.target.value)}
						/>
					</div>
				</div>

				{error !== null && <ErrorAlert message={error} className="mt-4" />}

				<Button type="submit" loading={submitting} fullWidth className="mt-6">
					{submitting ? 'Signing in…' : 'Sign in'}
				</Button>
			</form>
		</main>
	);
}
