import { useState } from 'react';
import { Banner } from '@/shared/ui/Banner';
import { useVersionInfo } from '@/shared/hooks';
import { usePermission, ORG_ADMIN } from '@/shared/auth/usePermission';

const DISMISSED_KEY = 'j1.updateBanner.dismissedVersion';

function readDismissed(): string | null {
	if (typeof window === 'undefined') return null;
	try {
		return window.localStorage.getItem(DISMISSED_KEY);
	} catch {
		return null;
	}
}

function writeDismissed(version: string): void {
	if (typeof window === 'undefined') return;
	try {
		window.localStorage.setItem(DISMISSED_KEY, version);
	} catch {
		/* ignore */
	}
}

/**
 * Shell banner announcing a newer app release.
 *
 * Admin-only: upgrading is an operator action (`jenticctl update` on the host),
 * so only an org admin sees the banner — a non-admin can't act on it and it
 * would just be noise. The current-version line in the user menu still shows for
 * everyone.
 *
 * Shows only when the backend reports `update_available` and the user has not
 * already dismissed *this* version — dismissal persists the dismissed version
 * string, so a later release re-shows the banner (dismissing 0.26 still surfaces
 * 0.27). Reads the same `useVersionInfo` cache slice as the UserMenu version
 * line (React Query dedupes), so mounting it here is free.
 */
export function UpdateBanner() {
	const isAdmin = usePermission(ORG_ADMIN);
	const { latest, update_available } = useVersionInfo();
	const [dismissed, setDismissed] = useState<string | null>(() => readDismissed());

	if (!isAdmin || !update_available || !latest || latest === dismissed) return null;

	const dismiss = () => {
		writeDismissed(latest);
		setDismissed(latest);
	};

	return (
		<Banner onDismiss={dismiss} dismissLabel="Dismiss update notice">
			<span>
				<span className="font-medium">jentic-one v{latest}</span> is available — run{' '}
				<code className="bg-muted text-foreground rounded px-1 py-0.5 font-mono text-xs">
					jenticctl update
				</code>{' '}
				to upgrade.
			</span>
		</Banner>
	);
}
