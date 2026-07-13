import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Button } from '@/shared/ui/Button';
import { AppLink } from '@/shared/ui/AppLink';
import { ROUTES } from '@/shared/app/routes';

/**
 * Wire contract for the advisory popup→opener connect signal (#598).
 *
 * This page is the *popup side* that posts the message. The opener side lives
 * in the credentials module (`runConnectFlow`), which owns the canonical
 * `OAUTH_CONNECT_MESSAGE_TYPE` constant. Shared code never imports modules/, so
 * this page carries a private copy of the same string literal; a regression
 * test (`OAuthPopupReturn.test.tsx`) pins the two equal. The message is
 * *advisory only* — no credential id, token, or reason — and the opener always
 * re-reads `GET /credentials/{id}` to learn the authoritative outcome.
 */
const OAUTH_CONNECT_MESSAGE_TYPE = 'jentic:oauth-connect' as const;

interface OAuthConnectMessage {
	type: typeof OAUTH_CONNECT_MESSAGE_TYPE;
	status: 'ok' | 'error';
}

/** True for window.open()-spawned popups; false for a same-tab fallback. */
function hasScriptOpener(): boolean {
	try {
		return window.opener != null && window.opener !== window;
	} catch {
		// Cross-origin opener access can throw; treat as "has an opener" since a
		// cross-origin opener is still script-spawned.
		return true;
	}
}

/**
 * Landing page for the OAuth connect popup.
 *
 * The backend callback (`GET /credentials/oauth/callback`) 303-redirects the
 * popup here after the IdP round-trip rather than emitting HTML from the API.
 * This page owns the user-facing "you can close this window" experience.
 *
 * Two signals fire on mount:
 *  1. An advisory `postMessage` to the opener (origin-restricted to our own
 *     origin) so the parent SPA can stop polling promptly (#598). The opener
 *     still confirms via the credentials API — this is a hint, not a truth.
 *  2. A `window.close()` attempt. Browsers only honour close for script-opened
 *     windows, so if the page lingers (popup-blocker fallback, same-tab, or a
 *     close the browser refused) we surface an explicit manual affordance
 *     instead of a dead-end static page (#601).
 *
 * Only a coarse `?status=ok|error` arrives — never a reason. Lives outside the
 * AuthGuard: the popup has no guaranteed session and we never read anything
 * privileged here.
 */
export function OAuthPopupReturn() {
	const [params] = useSearchParams();
	const isError = params.get('status') === 'error';
	const sameTab = !hasScriptOpener();
	// Surface the manual close/return affordance once an auto-close attempt has
	// been made but the window is still here (close blocked, or same-tab).
	const [closeBlocked, setCloseBlocked] = useState(false);
	const closeButtonRef = useRef<HTMLButtonElement>(null);

	// Notify the opener (advisory) so it can short-circuit its poll. Restrict the
	// target origin to our own — the opener is same-origin (SPA under /app), and
	// a strict origin prevents the message leaking to an unexpected document.
	useEffect(() => {
		try {
			const message: OAuthConnectMessage = {
				type: OAUTH_CONNECT_MESSAGE_TYPE,
				status: isError ? 'error' : 'ok',
			};
			window.opener?.postMessage(message, window.location.origin);
		} catch {
			// Best-effort: a blocked or cross-origin opener just means the parent
			// falls back to polling. Never throw out of the notify path.
		}
	}, [isError]);

	// Browsers only honour window.close() for script-opened windows. On success
	// close immediately; on error give the user a moment to read first. If the
	// window is still open after the attempt, reveal the manual affordance.
	useEffect(() => {
		const delay = isError ? 5000 : 0;
		let followUp: number | undefined;
		const timer = window.setTimeout(() => {
			window.close();
			// If close() was honoured this component is already gone; otherwise the
			// follow-up tick flips us to the manual-close state.
			followUp = window.setTimeout(() => setCloseBlocked(true), 150);
		}, delay);
		return () => {
			window.clearTimeout(timer);
			// Clear the inner follow-up too, so a navigation/unmount between the
			// close() attempt and the 150ms tick can't setState on an unmounted tree.
			if (followUp !== undefined) window.clearTimeout(followUp);
		};
	}, [isError]);

	const title = isError ? 'Sign-in failed' : 'Sign-in complete';
	const message = isError
		? 'Something went wrong. You can close this window and try again.'
		: 'You can close this window.';
	// Same-tab fallbacks have no opener to close them, so always offer the
	// affordance there; popups only show it once an auto-close was refused.
	const showAffordance = sameTab || closeBlocked;

	// When the affordance is revealed (dynamically, after the auto-close was
	// refused), move focus to the Close button so keyboard/AT users discover it
	// without hunting — the button lives outside the aria-live status region, so
	// its appearance is otherwise unannounced.
	useEffect(() => {
		if (showAffordance) closeButtonRef.current?.focus();
	}, [showAffordance]);

	return (
		<main className="bg-background text-foreground flex min-h-screen items-center justify-center px-4">
			<div
				className="border-border bg-card w-full max-w-sm rounded-xl border p-6 text-center shadow-sm"
				role={isError ? 'alert' : 'status'}
			>
				<h1 className="font-display text-lg font-semibold">{title}</h1>
				<p className="text-muted-foreground mt-2 text-sm">{message}</p>

				{showAffordance && (
					<div className="mt-5 flex flex-col items-stretch gap-2">
						<Button ref={closeButtonRef} type="button" onClick={() => window.close()}>
							Close window
						</Button>
						{sameTab && (
							<AppLink
								href={ROUTES.credentials}
								className="text-muted-foreground hover:text-foreground text-xs underline-offset-2 hover:underline"
							>
								Return to credentials
							</AppLink>
						)}
					</div>
				)}
			</div>
		</main>
	);
}
