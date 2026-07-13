import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '@/shared/auth/AuthContext';
import { ApiError } from '@/shared/api';
import { ROUTES } from '@/shared/app/routes';
import { Input } from '@/shared/ui/Input';
import { Label } from '@/shared/ui/Label';
import { Button } from '@/shared/ui/Button';
import { ErrorAlert } from '@/shared/ui/ErrorAlert';
import { MIN_PASSWORD_LENGTH } from '@/shared/auth/password';

/**
 * Password change page — serves two entry paths:
 *
 *  - **Forced** (`must_change_password=true`): the AuthGuard redirects here
 *    (e.g. an invited user redeeming a temporary password). The user must
 *    rotate before entering the app, so the secondary action is "Sign out".
 *  - **Voluntary**: a signed-in user picks "Change password" from the user
 *    menu (#594). Nothing is wrong with the current password, so the copy is
 *    neutral and the secondary action is "Cancel" — straight back to the app,
 *    never bounced through the forced gate.
 *
 * `/users/me:change-password` re-mints the token with the must_change_password
 * claim cleared and returns it; `changePassword` (AuthContext) adopts that fresh
 * token and refreshes /users/me. So once it resolves we hold a clean session and
 * can enter the app directly — no re-login round-trip, and no risk of the stale
 * `n: true` claim looping the AuthGuard back here.
 */
export function ChangePasswordPage() {
	const { user, changePassword, logout, mustChangePassword } = useAuth();
	const navigate = useNavigate();
	const [currentPassword, setCurrentPassword] = useState('');
	const [newPassword, setNewPassword] = useState('');
	const [confirm, setConfirm] = useState('');
	const [error, setError] = useState<string | null>(null);
	const [submitting, setSubmitting] = useState(false);

	// Wipe sensitive fields from React state when the page unmounts (navigate
	// away / sign out), so passwords don't linger in memory longer than needed.
	const wipe = useRef<() => void>(() => {});
	wipe.current = () => {
		setCurrentPassword('');
		setNewPassword('');
		setConfirm('');
	};
	useEffect(() => () => wipe.current(), []);

	// Move focus to the first field on mount so users — especially those the
	// AuthGuard *redirected* here mid-flow for a forced change — are oriented to
	// the new context instead of being left with focus on the previous page.
	const currentPasswordRef = useRef<HTMLInputElement>(null);
	useEffect(() => {
		currentPasswordRef.current?.focus();
	}, []);

	const handleSubmit = async (event: React.FormEvent) => {
		event.preventDefault();
		// Synchronous double-submit guard (the Button's loading prop only disables
		// after a render).
		if (submitting) {
			return;
		}
		setError(null);
		if (newPassword.length < MIN_PASSWORD_LENGTH) {
			setError(`New password must be at least ${MIN_PASSWORD_LENGTH} characters.`);
			return;
		}
		if (newPassword !== confirm) {
			setError('New password and confirmation do not match.');
			return;
		}
		setSubmitting(true);
		try {
			// On success this swaps in the re-minted token, so we hold a clean
			// session and can go straight to the app.
			await changePassword(currentPassword, newPassword);
			wipe.current();
			navigate(ROUTES.app, { replace: true });
		} catch (err) {
			if (err instanceof ApiError && err.status === 401) {
				// 401 invalid_credentials — the current password didn't match.
				setError('Current password is incorrect.');
			} else if (err instanceof ApiError && (err.status === 400 || err.status === 422)) {
				// 400 invalid_input / 422 validation_error — the new password failed
				// the server's policy (a rule the client doesn't mirror, or request
				// validation). Either way it's about the new password, not the
				// current one, so don't blame the current password.
				setError('New password does not meet the requirements.');
			} else {
				setError('Could not change password. Please try again.');
			}
			setSubmitting(false);
		}
	};

	return (
		<main className="bg-background text-foreground flex min-h-screen items-center justify-center px-4">
			<form
				onSubmit={handleSubmit}
				className="border-border bg-card w-full max-w-sm rounded-xl border p-6 shadow-sm"
				aria-labelledby="change-password-heading"
			>
				<h1 id="change-password-heading" className="font-display text-xl font-semibold">
					{mustChangePassword ? 'Set a new password' : 'Change your password'}
				</h1>
				<p className="text-muted-foreground mt-1 text-sm">
					{user?.email ? `Signed in as ${user.email}. ` : ''}
					{mustChangePassword
						? 'You must change your password before continuing.'
						: 'Enter your current password and choose a new one.'}
				</p>

				<div className="mt-6 space-y-4">
					<div className="space-y-1">
						<Label htmlFor="cp-current">Current password</Label>
						<Input
							id="cp-current"
							ref={currentPasswordRef}
							type="password"
							autoComplete="current-password"
							showPasswordToggle
							required
							value={currentPassword}
							onChange={(e) => setCurrentPassword(e.target.value)}
						/>
					</div>
					<div className="space-y-1">
						<Label htmlFor="cp-new">New password</Label>
						<Input
							id="cp-new"
							type="password"
							autoComplete="new-password"
							showPasswordToggle
							required
							minLength={MIN_PASSWORD_LENGTH}
							value={newPassword}
							onChange={(e) => setNewPassword(e.target.value)}
						/>
						<p className="text-muted-foreground text-xs">
							At least {MIN_PASSWORD_LENGTH} characters.
						</p>
					</div>
					<div className="space-y-1">
						<Label htmlFor="cp-confirm">Confirm new password</Label>
						<Input
							id="cp-confirm"
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
					{submitting
						? 'Saving…'
						: mustChangePassword
							? 'Set password'
							: 'Change password'}
				</Button>
				{mustChangePassword ? (
					<Button
						type="button"
						variant="ghost"
						fullWidth
						onClick={logout}
						className="mt-3"
					>
						Sign out
					</Button>
				) : (
					<Button
						type="button"
						variant="ghost"
						fullWidth
						onClick={() => {
							wipe.current();
							navigate(ROUTES.app, { replace: true });
						}}
						className="mt-3"
					>
						Cancel
					</Button>
				)}
			</form>
		</main>
	);
}
