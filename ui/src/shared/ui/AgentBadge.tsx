import { Bot } from 'lucide-react';
import { cn } from '@/shared/lib/utils';

/**
 * AgentBadge — a deterministic identity chip for an actor (agent / service
 * account). The background colour is hashed from the stable id so the same
 * actor always reads the same colour across every surface (table, detail,
 * activity rows); the glyph is the actor's initials, falling back to a bot
 * icon when there's no name.
 *
 * Ported from the jentic-mini agent-centre revamp and generalised into a
 * shared primitive so the agents table, detail page, and any future
 * dashboard/monitor surface can reuse one identity treatment.
 */

export type AgentBadgeSize = 'xs' | 'sm' | 'md' | 'lg';

/** Accent palette — keyed by a hash of the id so colours are stable per actor. */
const ACCENT_CLASSES = [
	'bg-accent-blue/15 text-accent-blue',
	'bg-accent-teal/15 text-accent-teal',
	'bg-accent-orange/15 text-accent-orange',
	'bg-accent-yellow/15 text-accent-yellow',
	'bg-primary/15 text-primary',
] as const;

const SIZE_CLASSES: Record<AgentBadgeSize, string> = {
	xs: 'h-5 w-5 text-[9px]',
	sm: 'h-7 w-7 text-[10px]',
	md: 'h-9 w-9 text-xs',
	lg: 'h-11 w-11 text-sm',
};

const ICON_SIZE: Record<AgentBadgeSize, string> = {
	xs: 'h-2.5 w-2.5',
	sm: 'h-3.5 w-3.5',
	md: 'h-4 w-4',
	lg: 'h-5 w-5',
};

/** djb2-style hash → palette index. Deterministic for a given id. */
function accentFor(id: string | undefined): string {
	if (!id) return 'bg-muted text-muted-foreground';
	let h = 5381;
	for (let i = 0; i < id.length; i++) h = (h * 33) ^ id.charCodeAt(i);
	return ACCENT_CLASSES[Math.abs(h) % ACCENT_CLASSES.length];
}

/** Up to two initials from a name (word-initials, else first two letters). */
export function agentInitials(name: string | undefined): string {
	if (!name) return '';
	const words = name
		.trim()
		.split(/[\s_-]+/)
		.filter(Boolean);
	if (words.length === 0) return '';
	if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
	return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

interface AgentBadgeProps {
	/** Stable id used to derive the deterministic accent colour. */
	id?: string;
	/** Display name used for the initials + the accessible label. */
	name?: string;
	/** Actor noun for the accessible label (e.g. "Agent", "Service account"). */
	kind?: string;
	size?: AgentBadgeSize;
	/** When provided, the badge renders as a button (e.g. navigate to detail). */
	onClick?: () => void;
	/** Dim the badge (e.g. an idle agent in a heatmap). */
	dimmed?: boolean;
	className?: string;
}

export function AgentBadge({
	id,
	name,
	kind = 'Agent',
	size = 'md',
	onClick,
	dimmed = false,
	className,
}: AgentBadgeProps) {
	const initials = agentInitials(name);
	const label = name ? `${kind} ${name}` : kind;

	const content = initials ? (
		<span className="font-semibold tracking-tight">{initials}</span>
	) : (
		<Bot className={ICON_SIZE[size]} aria-hidden />
	);

	const classes = cn(
		'inline-flex shrink-0 items-center justify-center rounded-lg font-mono select-none',
		SIZE_CLASSES[size],
		accentFor(id),
		dimmed && 'opacity-40',
		onClick && 'cursor-pointer transition-transform hover:scale-105',
		className,
	);

	if (onClick) {
		return (
			<button type="button" onClick={onClick} className={classes} aria-label={label}>
				{content}
			</button>
		);
	}

	return (
		<span className={classes} role="img" aria-label={label}>
			{content}
		</span>
	);
}
