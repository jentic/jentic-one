import React from 'react';
import { cn } from '@/shared/lib/utils';

export type Variant = 'default' | 'success' | 'warning' | 'danger' | 'pending' | 'neutral';

// A soft borderless pill: the hue tints the fill (10%) and the word; the word
// carries the meaning, so the tint is only a secondary cue. `warning` is a
// neutral tonal chip — a caution is a state to note, not an alarm, so only
// its (optional) dot is warm; `pending` (a person must act) keeps the
// stronger desaturated ochre.
const variantClasses: Record<Variant, string> = {
	default: 'bg-primary/10 text-primary',
	success: 'bg-success/10 text-success',
	warning: 'bg-surface-tonal text-foreground-lighter',
	danger: 'bg-danger/10 text-danger',
	pending: 'bg-warning/10 text-warning',
	// Neither good nor bad (e.g. a draft): grey, one step lighter than its surface.
	neutral: 'bg-surface-field text-foreground-sub',
};

const badgeDotClasses: Partial<Record<Variant, string>> = { warning: 'bg-caution' };

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
	variant?: Variant;
	/** Show a leading status dot in the badge's colour. */
	dot?: boolean;
	/** Monospace digits for codes, ids and counts (e.g. an HTTP status). */
	mono?: boolean;
}

export function Badge({
	variant = 'default',
	dot,
	mono,
	children,
	className,
	...props
}: BadgeProps) {
	return (
		<span
			data-variant={variant}
			className={cn(
				'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11.5px] leading-4 font-bold whitespace-nowrap tabular-nums',
				mono ? 'font-mono' : 'font-sans',
				variantClasses[variant],
				className,
			)}
			{...props}
		>
			{dot && (
				<span
					className={cn(
						'h-1.5 w-1.5 shrink-0 rounded-full',
						badgeDotClasses[variant] ?? 'bg-current',
					)}
					aria-hidden="true"
				/>
			)}
			{children}
		</span>
	);
}

export type StatusTone = 'success' | 'warning' | 'caution' | 'muted' | 'accent' | 'loading';

/**
 * Per tone: the word's colour, the dot, and the glyph's colour when an `icon`
 * replaces the dot. The warm tones never colour the word — `warning` (wants
 * action now) and `caution` (a state to note) keep neutral text and put the
 * low-chroma ochre on the dot/glyph only.
 */
const statusToneClasses: Record<StatusTone, { text: string; dot: string; icon: string }> = {
	success: { text: 'text-success', dot: 'bg-success', icon: 'text-success' },
	warning: { text: 'text-foreground-lighter', dot: 'bg-warning', icon: 'text-warning' },
	caution: { text: 'text-foreground-sub', dot: 'bg-caution', icon: 'text-caution' },
	muted: { text: 'text-muted-foreground', dot: 'border border-current', icon: '' },
	accent: { text: 'text-primary', dot: 'bg-primary', icon: '' },
	// In flight (e.g. "Adding…"): a small accent ring spins where the dot sits.
	loading: {
		text: 'text-primary',
		dot: 'h-3 w-3 border-[1.5px] border-primary/25 border-t-primary animate-spin motion-reduce:animate-none',
		icon: '',
	},
};

const statusSizeClasses = { xs: 'text-[11.5px]', sm: 'text-xs' } as const;

type StatusIcon = React.ComponentType<{ className?: string; 'aria-hidden'?: boolean | 'true' }>;

interface StatusTextProps extends React.HTMLAttributes<HTMLSpanElement> {
	tone: StatusTone;
	/** `xs` 11.5px (cards), `sm` 12px (rows, default). */
	size?: keyof typeof statusSizeClasses;
	/** A semantic glyph in place of the dot (pause = suspended, shield-off =
	 * blocked, key = no credential, moon = inactive). Tinted by the tone. */
	icon?: StatusIcon;
	/** Regular weight — for a fact inside a meta line rather than a status. */
	plain?: boolean;
}

/**
 * A live state as a dot (or a glyph) and a word, without a container — for a
 * card's one status line or a table's status cell, where a filled pill
 * competes with the row's own title. A muted tone draws a hollow dot: the
 * thing is idle, not faulty. The word always carries the meaning; the dot or
 * glyph is a secondary cue.
 */
export function StatusText({
	tone,
	size = 'sm',
	icon: Icon,
	plain,
	children,
	className,
	...props
}: StatusTextProps) {
	const classes = statusToneClasses[tone];
	return (
		<span
			data-tone={tone}
			className={cn(
				'inline-flex items-center gap-1.5 whitespace-nowrap',
				plain ? 'font-medium' : 'font-bold',
				statusSizeClasses[size],
				classes.text,
				className,
			)}
			{...props}
		>
			{Icon ? (
				<Icon className={cn('h-3.5 w-3.5 shrink-0', classes.icon)} aria-hidden="true" />
			) : (
				<span
					className={cn(
						'shrink-0 rounded-full',
						tone === 'loading' ? '' : 'h-1.5 w-1.5',
						classes.dot,
					)}
					aria-hidden="true"
				/>
			)}
			{children}
		</span>
	);
}

interface StatusChipProps extends React.HTMLAttributes<HTMLSpanElement> {
	/** Tints the glyph only; the chip and its word stay neutral. */
	tone?: 'caution' | 'warning' | 'neutral' | 'success' | 'danger';
	icon: StatusIcon;
}

const chipIconTone: Record<NonNullable<StatusChipProps['tone']>, string> = {
	caution: 'text-caution',
	warning: 'text-warning',
	neutral: 'text-foreground-sub',
	success: 'text-success',
	danger: 'text-danger',
};

/**
 * A state as a neutral tonal chip with a semantic glyph — the header/row
 * counterpart of `StatusText` (e.g. "Suspended" beside a sheet title). Only
 * the glyph is tinted, so the chip never shouts; the word carries the meaning.
 */
export function StatusChip({
	tone = 'caution',
	icon: Icon,
	children,
	className,
	...props
}: StatusChipProps) {
	return (
		<span
			data-tone={tone}
			className={cn(
				'bg-surface-tonal text-foreground-lighter inline-flex items-center gap-1.5 rounded-full py-0.5 pr-2.5 pl-2 text-[11.5px] leading-4 font-semibold whitespace-nowrap',
				className,
			)}
			{...props}
		>
			<Icon className={cn('h-3.5 w-3.5 shrink-0', chipIconTone[tone])} aria-hidden="true" />
			{children}
		</span>
	);
}

interface TagProps extends React.HTMLAttributes<HTMLSpanElement> {
	/** Optional leading glyph that names the category at a glance. */
	icon?: React.ComponentType<{ className?: string }>;
}

/**
 * A neutral category label (an auth type, a kind) — grey, because a category
 * is neither good nor bad, so it takes none of the status colours.
 */
export function Tag({ icon: Icon, children, className, ...props }: TagProps) {
	return (
		<span
			className={cn(
				'bg-surface-field text-foreground-sub inline-flex shrink-0 items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] leading-none font-semibold whitespace-nowrap',
				className,
			)}
			{...props}
		>
			{Icon && <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />}
			{children}
		</span>
	);
}

// A borderless neutral chip — only the word is tinted, and GET (the most
// common verb) stays neutral so a long list doesn't shout. The method word is
// always printed, so the tint is a secondary cue (PUT/PATCH share one).
const methodChipText: Record<string, string> = {
	GET: 'text-foreground-sub',
	POST: 'text-method-post',
	PUT: 'text-method-put',
	PATCH: 'text-method-put',
	DELETE: 'text-method-delete',
};

export function MethodBadge({ method, className }: { method?: string | null; className?: string }) {
	const m = method?.toUpperCase() || '?';
	return (
		<span
			data-method={m}
			className={cn(
				// Fixed width + no shrink so the following path never shifts as the
				// method label changes (GET vs DELETE) down a list of operations.
				'bg-surface-chip inline-flex h-5 w-[58px] shrink-0 items-center justify-center rounded-[5px] text-center font-mono text-[10.5px] leading-5 font-semibold',
				methodChipText[m] ?? 'text-foreground-sub',
				className,
			)}
		>
			{m}
		</span>
	);
}

export function StatusBadge({ status }: { status?: number | null }) {
	if (!status) return null;
	const variant: Variant =
		status >= 500
			? 'danger'
			: status >= 400
				? 'warning'
				: status >= 200 && status < 300
					? 'success'
					: 'default';
	return (
		<Badge variant={variant} mono>
			{status}
		</Badge>
	);
}
