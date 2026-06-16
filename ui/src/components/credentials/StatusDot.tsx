import type { ReactNode } from 'react';
import { HoverTooltip } from '@/components/ui/HoverTooltip';

/**
 * Single-purpose status indicator for credential rows and OAuth account chips.
 *
 * The credential surface has four meaningful states (matching how the
 * audit / health pipeline talks about them):
 *
 *  - **`ok`** — positive evidence the credential works: the broker observed a
 *    `< 400` upstream call, the /test probe came back `ok`, or (for Pipedream)
 *    `oauth_broker_accounts.healthy = 1`. Green.
 *  - **`broken`** — authoritative bad state. Set by 401/403 from the broker on
 *    a real upstream call, by the /test endpoint returning hint=unauthorized,
 *    or by a rejected Pipedream grant (`healthy = 0`). Red.
 *  - **`neutral`** — never exercised. No call, no probe, no health write yet.
 *    This is the *default* resting state of a freshly-added credential and is
 *    deliberately distinct from `unknown`: it's not a problem, just "we have no
 *    signal because nothing has used it". Muted/quiet so it doesn't read as a
 *    warning.
 *  - **`unknown`** — we *tried* to determine health and couldn't (a /test that
 *    timed out or hit a network error). A genuinely ambiguous result the user
 *    asked for, so it gets a slightly more prominent amber than `neutral`.
 *
 * The semantic colour comes from the design tokens (`bg-success` / `bg-danger`
 * / `bg-warning` / `bg-muted-foreground`) so the dot stays consistent with the
 * rest of the UI's status language. The trigger carries an `aria-label` so
 * screen readers don't see a "decorative blob" — credential health is
 * meaningful information — and the same copy is surfaced visually through a
 * styled `HoverTooltip` (title + optional detail line) instead of the flat
 * native `title` attribute.
 */
export type CredentialStatus = 'ok' | 'broken' | 'neutral' | 'unknown';

interface StatusDotProps {
	status: CredentialStatus;
	/**
	 * Short headline read out as the `aria-label` and shown bold at the top of
	 * the tooltip — e.g. "Working" / "Rejected" / "Never used".
	 */
	label: string;
	/**
	 * Optional secondary line shown under `label` in the tooltip (e.g.
	 * "Broker saw a healthy call · checked 5m ago"). Purely visual — not part
	 * of the accessible name, which stays concise.
	 */
	detail?: ReactNode;
	/** Optional size override; defaults to 8px which lines up with text-xs. */
	size?: 'sm' | 'md';
	/** Optional ReactNode rendered to the right of the dot — e.g. "synced 5m ago". */
	children?: ReactNode;
	className?: string;
}

const TONE: Record<CredentialStatus, string> = {
	ok: 'bg-success',
	broken: 'bg-danger',
	// Amber: an answered-but-ambiguous probe. Falls back gracefully if the
	// theme has no `warning` token (older palettes) via the arbitrary value.
	unknown: 'bg-warning',
	// Quiet grey: the resting "nothing has happened yet" state.
	neutral: 'bg-muted-foreground/50',
};

const SIZE = {
	sm: 'h-1.5 w-1.5',
	md: 'h-2 w-2',
} as const;

export function StatusDot({
	status,
	label,
	detail,
	size = 'sm',
	children,
	className,
}: StatusDotProps) {
	const tooltip = (
		<span className="block leading-snug">
			<span className="text-popover-foreground font-medium">{label}</span>
			{detail != null && (
				<span className="text-muted-foreground mt-0.5 block text-[11px]">{detail}</span>
			)}
		</span>
	);

	return (
		<HoverTooltip
			content={tooltip}
			triggerClassName="inline-flex"
			className={className}
			role="status"
		>
			<span className="inline-flex items-center gap-1.5" aria-label={label} role="img">
				<span
					className={`inline-block shrink-0 rounded-full ${SIZE[size]} ${TONE[status]}`}
				/>
				{children}
			</span>
		</HoverTooltip>
	);
}
