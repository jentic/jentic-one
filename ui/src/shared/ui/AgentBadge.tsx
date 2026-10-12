import { createContext, useContext, type ReactNode } from 'react';
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
 * A shared primitive so the agents surfaces and any future
 * agents/monitor surface can reuse one identity treatment.
 */

export type AgentBadgeSize = 'xs' | 'sm' | 'md' | 'lg';

export type AgentBadgeShape = 'square' | 'circle';

const SIZE_CLASSES: Record<AgentBadgeSize, string> = {
	xs: 'h-5 w-5 text-[8.5px]',
	sm: 'h-7 w-7 text-[10.5px]',
	md: 'h-9 w-9 text-xs',
	lg: 'h-11 w-11 text-sm',
};

/** The square's corner per size; a circle is round at every size. */
const SQUARE_RADIUS: Record<AgentBadgeSize, string> = {
	xs: 'rounded-[6px]',
	sm: 'rounded-[7px]',
	md: 'rounded-field',
	lg: 'rounded-[11px]',
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

/**
 * Fleet-aware initials by actor id (see `smartInitials`), so near-identical
 * names read apart on every badge under the provider. A badge whose id is not
 * in the map falls back to {@link agentInitials}.
 */
const AgentInitialsContext = createContext<ReadonlyMap<string, string> | null>(null);

export function AgentInitialsProvider({
	initials,
	children,
}: {
	initials: ReadonlyMap<string, string>;
	children: ReactNode;
}) {
	return (
		<AgentInitialsContext.Provider value={initials}>{children}</AgentInitialsContext.Provider>
	);
}

/** Three or four letters step the glyph down so they still fit the tile. */
const GLYPH_SCALE: Record<number, string | undefined> = { 3: '0.84em', 4: '0.7em' };

interface AgentBadgeProps {
	/** Stable id used to derive the deterministic accent colour. */
	id?: string;
	/** Display name used for the initials + the accessible label. */
	name?: string;
	/** Actor noun for the accessible label (e.g. "Agent"). */
	kind?: string;
	size?: AgentBadgeSize;
	/** `circle` sets an agent apart from the rounded-square API marks beside it. */
	shape?: AgentBadgeShape;
	/** When provided, the badge renders as a button (e.g. navigate to detail). */
	onClick?: () => void;
	/** Dim the badge (e.g. an idle agent in a heatmap). */
	dimmed?: boolean;
	/** Explicit initials (up to four letters), over the provider's and the name's. */
	initials?: string;
	className?: string;
}

export function AgentBadge({
	id,
	name,
	kind = 'Agent',
	size = 'md',
	shape = 'square',
	onClick,
	dimmed = false,
	initials: initialsOverride,
	className,
}: AgentBadgeProps) {
	const fleetInitials = useContext(AgentInitialsContext);
	const initials =
		initialsOverride ?? (id ? fleetInitials?.get(id) : undefined) ?? agentInitials(name);
	const label = name ? `${kind} ${name}` : kind;

	const content = initials ? (
		<span
			className="font-heading font-bold tracking-[0.01em]"
			style={
				GLYPH_SCALE[initials.length]
					? { fontSize: GLYPH_SCALE[initials.length], letterSpacing: '-0.02em' }
					: undefined
			}
		>
			{initials}
		</span>
	) : (
		<Bot className={ICON_SIZE[size]} aria-hidden />
	);

	const tone = id ? avatarToneIndex(id) : null;
	const style = tone == null ? undefined : avatarToneStyle(tone);
	const classes = cn(
		'inline-flex shrink-0 items-center justify-center leading-none select-none',
		SIZE_CLASSES[size],
		shape === 'circle' ? 'rounded-full' : SQUARE_RADIUS[size],
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
