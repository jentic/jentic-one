import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '@/shared/auth/AuthContext';
import { ApiError } from '@/shared/api';
import { ROUTES } from '@/shared/app/routes';
import { Input } from '@/shared/ui/Input';
import { Label } from '@/shared/ui/Label';
import { Button } from '@/shared/ui/Button';
import { ErrorAlert } from '@/shared/ui/ErrorAlert';
import { MIN_PASSWORD_LENGTH } from '@/shared/auth/password';

/**
 * Invite redemption / finish-account page.
 *
 * An admin creates a user (`POST /users`), which issues a one-time invite token,
 * and sends the invitee a link to this page carrying `?token=<invite_token>`.
 * Here the invitee sets their initial password; `redeemInvite` calls
 * `POST /users:redeem-invite`, which returns a JWT that we adopt (auto-login),
 * landing them straight in the app.
 *
 * This is an auth-only screen (like Login/Setup): it intentionally bypasses
 * `PageShell` and owns its own centred card. It lives OUTSIDE the AuthGuard —
 * the invitee has no session yet.
 */
export function RedeemInvitePage() {
	const { redeemInvite } = useAuth();
	const navigate = useNavigate();
	const [searchParams] = useSearchParams();
	const token = searchParams.get('token') ?? '';

	const [password, setPassword] = useState('');
	const [confirm, setConfirm] = useState('');
	const [error, setError] = useState<string | null>(null);
	const [submitting, setSubmitting] = useState(false);

	// Wipe sensitive fields on unmount so passwords don't linger in memory.
	const wipe = useRef<() => void>(() => {});
	wipe.current = () => {
		setPassword('');
		setConfirm('');
	};
	useEffect(() => () => wipe.current(), []);

	const passwordRef = useRef<HTMLInputElement>(null);
	useEffect(() => {
		passwordRef.current?.focus();
	}, []);

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault();
		if (submitting) {
			return;
		}
		setError(null);
		if (!token) {
			setError('This invite link is missing its token. Ask your admin to resend it.');
			return;
		}
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
			await redeemInvite(token, password);
			wipe.current();
			navigate(ROUTES.app, { replace: true });
		} catch (err) {
			if (err instanceof ApiError && (err.status === 400 || err.status === 422)) {
				// 400/422 — invalid/expired token or the password failed policy.
				setError(
					'This invite is invalid or has expired, or the password does not meet the requirements. Ask your admin to resend the invite.',
				);
			} else {
				setError('Could not finish setting up your account. Please try again.');
			}
			setSubmitting(false);
		}
	};

	return (
		<main className="bg-background text-foreground flex min-h-screen items-center justify-center px-4">
			<form
				onSubmit={handleSubmit}
				className="border-border bg-card w-full max-w-sm rounded-xl border p-6 shadow-sm"
				aria-labelledby="redeem-invite-heading"
			>
				<h1 id="redeem-invite-heading" className="font-display text-xl font-semibold">
					Finish setting up your account
				</h1>
				<p className="text-muted-foreground mt-1 text-sm">
					Choose a password to activate your account and sign in.
				</p>

				<div className="mt-6 space-y-4">
					<div className="space-y-1">
						<Label htmlFor="ri-password">Password</Label>
						<Input
							id="ri-password"
							ref={passwordRef}
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
						<Label htmlFor="ri-confirm">Confirm password</Label>
						<Input
							id="ri-confirm"
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
					{submitting ? 'Setting up…' : 'Activate account'}
				</Button>
			</form>
		</main>
	);
}
