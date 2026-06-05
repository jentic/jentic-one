import type { ReactNode } from 'react';

/**
 * Single-purpose status indicator for credential rows and OAuth account chips.
 *
 * The credential surface has three meaningful states (matching how the
 * audit / health pipeline talks about them):
 *
 *  - **`ok`** — credential is fresh, healthy, OR has never been probed.
 *    Green when we have positive evidence (last_used_at recent or oauth_broker_accounts.healthy=1);
 *    a muted neutral when we have no signal yet (the "not probed" case).
 *  - **`broken`** — authoritative bad state. Set by 401/403 from the broker on a real
 *    upstream call, OR by the /test endpoint returning hint=unauthorized.
 *  - **`unknown`** — explicit "we tried but couldn't tell". Used only when the /test
 *    endpoint returns hint=timeout / hint=network_error etc.; we don't conflate this
 *    with "ok" because the user asked us a direct question.
 *
 * The semantic colour comes from the design tokens (`bg-success` / `bg-danger` /
 * `bg-muted-foreground`) so the dot stays consistent with the rest of the UI's
 * status language. The `aria-label` is required so screen readers don't see
 * "decorative blob" — credential health is meaningful information.
 */
export type CredentialStatus = 'ok' | 'broken' | 'unknown';

interface StatusDotProps {
	status: CredentialStatus;
	/** Tooltip-style help text that ALSO gets read out as the aria-label. */
	label: string;
	/** Optional size override; defaults to 8px which lines up with text-xs. */
	size?: 'sm' | 'md';
	/** Optional ReactNode rendered to the right of the dot — e.g. "synced 5m ago". */
	children?: ReactNode;
	className?: string;
}

const TONE: Record<CredentialStatus, string> = {
	ok: 'bg-success',
	broken: 'bg-danger',
	unknown: 'bg-muted-foreground/60',
};

const SIZE = {
	sm: 'h-1.5 w-1.5',
	md: 'h-2 w-2',
} as const;

export function StatusDot({ status, label, size = 'sm', children, className }: StatusDotProps) {
	return (
		<span className={`inline-flex items-center gap-1.5 ${className ?? ''}`} title={label}>
			<span
				role="img"
				aria-label={label}
				className={`inline-block shrink-0 rounded-full ${SIZE[size]} ${TONE[status]}`}
			/>
			{children}
		</span>
	);
}
