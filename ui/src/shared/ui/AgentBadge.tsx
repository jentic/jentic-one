import { Bot } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import { avatarToneIndex, avatarToneStyle } from '@/shared/ui/avatarPalette';

/**
 * AgentBadge — a deterministic identity chip for an actor (agent / service
 * account). The pastel tile is hashed from the stable id so the same actor
 * always reads the same colour across every surface (table, detail, activity
 * rows) — the same palette and type as `VendorIcon`, so APIs and agents share
 * one identity grammar. The glyph is the actor's initials, falling back to a
 * bot icon when there's no name.
 *
 * A shared primitive so the agents table, detail page, and any future
 * agents/monitor surface can reuse one identity treatment.
 */

export type AgentBadgeSize = 'xs' | 'sm' | 'md' | 'lg';

const SIZE_CLASSES: Record<AgentBadgeSize, string> = {
	xs: 'h-5 w-5 rounded-[6px] text-[8.5px]',
	sm: 'h-7 w-7 rounded-[7px] text-[10.5px]',
	md: 'h-9 w-9 rounded-field text-xs',
	lg: 'h-11 w-11 rounded-[11px] text-sm',
};

const ICON_SIZE: Record<AgentBadgeSize, string> = {
	xs: 'h-2.5 w-2.5',
	sm: 'h-3.5 w-3.5',
	md: 'h-4 w-4',
	lg: 'h-5 w-5',
};

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
	/** Actor noun for the accessible label (e.g. "Agent"). */
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
		<span className="font-heading font-bold tracking-[0.01em]">{initials}</span>
	) : (
		<Bot className={ICON_SIZE[size]} aria-hidden />
	);

	const tone = id ? avatarToneIndex(id) : null;
	const style = tone == null ? undefined : avatarToneStyle(tone);
	const classes = cn(
		'inline-flex shrink-0 items-center justify-center leading-none select-none',
		SIZE_CLASSES[size],
		tone == null && 'bg-muted text-muted-foreground',
		dimmed && 'opacity-40',
		onClick && 'cursor-pointer transition-transform hover:scale-105',
		className,
	);

	if (onClick) {
		return (
			<button
				type="button"
				onClick={onClick}
				className={classes}
				style={style}
				data-tone={tone ?? undefined}
				aria-label={label}
			>
				{content}
			</button>
		);
	}

	return (
		<span
			className={classes}
			style={style}
			data-tone={tone ?? undefined}
			role="img"
			aria-label={label}
		>
			{content}
		</span>
	);
}
