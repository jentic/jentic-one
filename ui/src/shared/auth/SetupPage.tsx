import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useAuth } from '@/shared/auth/AuthContext';
import { ApiError, HEALTH_QUERY_KEY } from '@/shared/api';
import { ROUTES } from '@/shared/app/routes';
import { Input } from '@/shared/ui/Input';
import { Label } from '@/shared/ui/Label';
import { Button } from '@/shared/ui/Button';
import { ErrorAlert } from '@/shared/ui/ErrorAlert';
import { MIN_PASSWORD_LENGTH } from '@/shared/auth/password';

/**
 * First-run setup. Reached only while no account exists (the SetupGate routes
 * here when health reports setup_required). Creates the first admin via the
 * one-time `POST /users:create-admin` endpoint and adopts the returned session
 * (auto-login), so the operator lands straight in the app.
 *
 * The endpoint self-closes once any user exists: a racing second submit gets a
 * 410, which we treat as "already set up" and bounce to the login screen.
 */
export function SetupPage() {
	const { createAdmin } = useAuth();
	const navigate = useNavigate();
	const queryClient = useQueryClient();
	const [email, setEmail] = useState('');
	const [password, setPassword] = useState('');
	const [confirm, setConfirm] = useState('');
	const [error, setError] = useState<string | null>(null);
	const [submitting, setSubmitting] = useState(false);

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault();
		// Guard synchronously: the Button's `loading` prop only disables after a
		// render, so a fast double-Enter could otherwise fire two create-admin
		// POSTs (the second racing into the now-closed 410 path).
		if (submitting) {
			return;
		}
		setError(null);
		if (password.length < MIN_PASSWORD_LENGTH) {
			setError(`Password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
			return;
		}
		if (password !== confirm) {
			setError('Password and confirmation do not match.');
			return;
		}
		setSubmitting(true);
		try {
			await createAdmin({ email, password });
			navigate(ROUTES.app, { replace: true });
		} catch (err) {
			if (err instanceof ApiError && err.status === 410) {
				// Setup already completed (likely a concurrent operator won). The
				// account exists now — refresh the health flag so SetupGate routes
				// to sign-in instead of bouncing us back here on the stale value.
				setError('Setup is already complete. Please sign in.');
				await queryClient.invalidateQueries({ queryKey: HEALTH_QUERY_KEY });
				navigate(ROUTES.login, { replace: true });
			} else if (err instanceof ApiError && err.status === 422) {
				setError('Please check the email and password and try again.');
			} else {
				setError('Could not complete setup. Please try again.');
			}
			setSubmitting(false);
		}
	};

	return (
		<main className="bg-background text-foreground flex min-h-screen items-center justify-center px-4">
			<form
				onSubmit={handleSubmit}
				className="border-border bg-card w-full max-w-sm rounded-xl border p-6 shadow-sm"
				aria-labelledby="setup-heading"
			>
				<h1 id="setup-heading" className="font-display text-xl font-semibold">
					Welcome to Jentic One
				</h1>
				<p className="text-muted-foreground mt-1 text-sm">
					Create the first administrator account to finish setup.
				</p>

				<div className="mt-6 space-y-4">
					<div className="space-y-1">
						<Label htmlFor="setup-email">Email</Label>
						<Input
							id="setup-email"
							type="email"
							autoComplete="username"
							required
							value={email}
							onChange={(e) => setEmail(e.target.value)}
						/>
					</div>
					<div className="space-y-1">
						<Label htmlFor="setup-password">Password</Label>
						<Input
							id="setup-password"
							type="password"
							autoComplete="new-password"
							showPasswordToggle
							required
							minLength={MIN_PASSWORD_LENGTH}
							value={password}
							onChange={(e) => setPassword(e.target.value)}
						/>
						<p className="text-muted-foreground text-xs">
							At least {MIN_PASSWORD_LENGTH} characters.
						</p>
					</div>
					<div className="space-y-1">
						<Label htmlFor="setup-confirm">Confirm password</Label>
						<Input
							id="setup-confirm"
							type="password"
							autoComplete="new-password"
							showPasswordToggle
							required
							value={confirm}
							onChange={(e) => setConfirm(e.target.value)}
						/>
					</div>
				</div>

				{error !== null && <ErrorAlert message={error} className="mt-4" />}

				<Button type="submit" loading={submitting} fullWidth className="mt-6">
					{submitting ? 'Creating account…' : 'Create admin account'}
				</Button>
			</form>
		</main>
	);
}
