/**
 * LogList / LogRow — the one row layout every Activity source renders.
 *
 *   Time | ● Event sentence            | Actor | Subject | Detail | Action
 *          secondary line (reason, error, diff)
 *
 * Everything, API calls, Jobs and the Audit log differ only in what fills the
 * cells, so a reader learns the log once. The layout follows the LIST's width,
 * not the viewport's (a container query): wide, the cells line up under a
 * column header; narrow — a phone, or the list squeezed beside the docked
 * detail pane — the same cells reflow into a sentence over a muted meta line.
 * It's one DOM either way (the meta wrapper is `display: contents` when
 * wide), so nothing is rendered twice for assistive tech or tests.
 *
 * Rows are `role="link"` divs rather than buttons because a row can carry its
 * own control (Everything's Acknowledge); a click that starts on a nested
 * control is left to it. `data-log-row` marks steppable rows for j/k.
 */
import {
	Fragment,
	useEffect,
	type CSSProperties,
	type KeyboardEvent,
	type MouseEvent,
	type ReactNode,
	type RefObject,
} from 'react';
import { AlertTriangle, Loader2, XCircle } from 'lucide-react';
import { formatStreamDayLabel, formatStreamTime, streamDayKey } from '@/shared/lib';
import { cn } from '@/shared/lib/utils';
import type { LogTone } from '@/modules/monitor/lib/logVocabulary';

export type { LogTone };

/* ------------------------------------------------------------------ */
/* Status glyph — the one status vocabulary                            */
/* ------------------------------------------------------------------ */

/**
 * ok       it worked (a completed call, a finished job)
 * fail     it didn't (failed call/job, error event, failed sign-in)
 * warn     worth a look (warnings, cancellations, destructive admin actions)
 * running  still going (queued / running jobs)
 * neutral  a plain record (most audit entries, informational events)
 *
 * Weight follows importance: the everyday outcomes are quiet dots, the ones
 * that need a reader's eye are glyphs.
 */
export function StatusGlyph({ tone, label }: { tone: LogTone; label: string }) {
	return (
		<span className="flex h-5 w-4 shrink-0 items-center justify-center">
			{tone === 'fail' ? (
				<XCircle className="text-danger h-4 w-4" aria-hidden="true" />
			) : tone === 'warn' ? (
				<AlertTriangle className="text-warning h-3.5 w-3.5" aria-hidden="true" />
			) : tone === 'running' ? (
				<Loader2 className="text-primary h-3.5 w-3.5 animate-spin" aria-hidden="true" />
			) : (
				<span
					aria-hidden="true"
					className={cn(
						'h-2 w-2 rounded-full',
						tone === 'ok' ? 'bg-success' : 'bg-muted-foreground/40',
					)}
				/>
			)}
			<span className="sr-only">{label}</span>
		</span>
	);
}

/* ------------------------------------------------------------------ */
/* Row                                                                 */
/* ------------------------------------------------------------------ */

/** The wide layout's columns; `--log-action` is sized per source by LogList. */
const WIDE_COLS = '@3xl:grid-cols-[4.5rem_minmax(0,1fr)_9.5rem_10.5rem_6.5rem_var(--log-action)]';

export interface LogRowProps {
	tsMs: number;
	tone: LogTone;
	/** Screen-reader status word ("Failed", "Completed", "Running"). */
	statusLabel: string;
	/** The plain-language sentence. */
	title: ReactNode;
	/** Monospace sentence (an operation id) — rendered as one text node. */
	mono?: boolean;
	/** Muted second line under the sentence: reason, change, event detail. */
	secondary?: ReactNode;
	/** Red second line — the error, visible without opening the row. */
	error?: string | null;
	actor?: ReactNode;
	subject?: ReactNode;
	detail?: ReactNode;
	/** Inline control (Acknowledge) or affordance (chevron). */
	action?: ReactNode;
	/** Accessible name of the row. */
	label: string;
	onOpen: () => void;
	/** The row whose record the detail pane shows. */
	active?: boolean;
	/** A row revealed inside an expanded run (indented, not steppable). */
	nested?: boolean;
	/** Dealt with already — a failure keeps only a faint accent. */
	muted?: boolean;
	/** Exclude from j/k stepping (e.g. a fold/unfold row). */
	steppable?: boolean;
}

function fromControl(target: EventTarget): boolean {
	return (target as HTMLElement).closest('button, a, [role="button"]') != null;
}

export function LogRow({
	tsMs,
	tone,
	statusLabel,
	title,
	mono,
	secondary,
	error,
	actor,
	subject,
	detail,
	action,
	label,
	onOpen,
	active,
	nested,
	muted,
	steppable = true,
}: LogRowProps) {
	const failure = tone === 'fail';
	const sep = (
		<span aria-hidden="true" className="@3xl:hidden">
			·
		</span>
	);

	return (
		<li
			className={cn(
				'border-border/40 relative border-b last:border-b-0',
				nested && 'bg-muted/25',
				failure && !muted && 'bg-danger/[0.035]',
			)}
		>
			{/* Left accent: the open row wins, then an unhandled failure. */}
			<span
				aria-hidden="true"
				className={cn(
					'absolute inset-y-0 left-0 w-0.5',
					active
						? 'bg-primary'
						: failure
							? muted
								? 'bg-danger/35'
								: 'bg-danger'
							: 'bg-transparent',
				)}
			/>
			<div
				role="link"
				tabIndex={0}
				aria-label={label}
				aria-current={active ? 'true' : undefined}
				data-log-row={steppable ? '' : undefined}
				onClick={(e: MouseEvent) => {
					if (!fromControl(e.target)) onOpen();
				}}
				onKeyDown={(e: KeyboardEvent) => {
					if ((e.key === 'Enter' || e.key === ' ') && !fromControl(e.target)) {
						e.preventDefault();
						onOpen();
					}
				}}
				className={cn(
					'grid scroll-mt-[calc(var(--log-top,0px)+4rem)] scroll-mb-4 grid-cols-[minmax(0,1fr)_auto] items-start gap-x-3 gap-y-1 px-3 py-2.5 sm:px-4',
					WIDE_COLS,
					'@3xl:items-center @3xl:gap-x-4',
					'hover:bg-muted/50 focus-visible:ring-ring cursor-pointer transition-colors outline-none focus-visible:ring-2 focus-visible:ring-inset',
					active && 'bg-primary/[0.06] hover:bg-primary/[0.08]',
					nested && 'pl-6 @3xl:pl-4',
				)}
			>
				{/* The event: glyph + sentence (+ secondary / error). */}
				<div
					className={cn(
						'col-start-1 row-start-1 flex min-w-0 items-start gap-2.5 @3xl:col-start-2',
						nested && '@3xl:pl-4',
					)}
				>
					<StatusGlyph tone={tone} label={statusLabel} />
					<div className="min-w-0 flex-1">
						<p
							className={cn(
								'text-foreground truncate',
								mono ? 'font-mono text-[13px]' : 'text-sm',
								failure && !muted ? 'font-semibold' : 'font-medium',
							)}
						>
							{title}
						</p>
						{error ? (
							<p className="text-danger mt-0.5 truncate text-xs" title={error}>
								{error}
							</p>
						) : secondary ? (
							<p className="text-muted-foreground mt-0.5 truncate text-xs">
								{secondary}
							</p>
						) : null}
					</div>
				</div>

				{/* Meta: a muted line under the sentence when narrow; its own
				    columns when wide (the wrapper dissolves). */}
				<div className="text-muted-foreground col-start-1 row-start-2 flex min-w-0 items-center gap-x-1.5 pl-6.5 text-xs @3xl:contents">
					<time
						dateTime={new Date(tsMs).toISOString()}
						className="shrink-0 font-mono tabular-nums @3xl:col-start-1 @3xl:row-start-1"
					>
						{formatStreamTime(tsMs)}
					</time>
					{actor != null && (
						<>
							{sep}
							<span className="text-foreground/80 min-w-0 truncate font-medium @3xl:col-start-3 @3xl:row-start-1">
								{actor}
							</span>
						</>
					)}
					{subject != null && (
						<>
							{sep}
							<span className="flex min-w-0 shrink items-center gap-1.5 truncate @3xl:col-start-4 @3xl:row-start-1">
								{subject}
							</span>
						</>
					)}
					{detail != null && (
						<>
							{sep}
							<span className="shrink-0 font-mono tabular-nums @3xl:col-start-5 @3xl:row-start-1 @3xl:justify-self-end">
								{detail}
							</span>
						</>
					)}
				</div>

				{action != null && (
					<div className="col-start-2 row-span-2 row-start-1 flex items-center justify-end self-center @3xl:col-start-6 @3xl:row-span-1">
						{action}
					</div>
				)}
			</div>
		</li>
	);
}

/* ------------------------------------------------------------------ */
/* List                                                                */
/* ------------------------------------------------------------------ */

export interface LogColumns {
	/** Column-header labels for the wide layout. */
	actor: string;
	subject: string;
	detail: string;
}

export function LogList({
	ariaLabel,
	columns,
	actionWidth = '1rem',
	header,
	footer,
	children,
	className,
}: {
	ariaLabel: string;
	columns: LogColumns;
	/** Width of the trailing action column (Everything needs room for Acknowledge). */
	actionWidth?: string;
	/** Above the column header (Everything's live bar). */
	header?: ReactNode;
	/** Below the rows (Load older). */
	footer?: ReactNode;
	children: ReactNode;
	className?: string;
}) {
	return (
		<section
			aria-label={ariaLabel}
			className={cn(
				'border-border bg-card @container overflow-clip rounded-xl border',
				className,
			)}
			style={{ '--log-action': actionWidth } as CSSProperties}
		>
			{header}
			{/* Visual column labels only — each row's link name carries its meaning. */}
			<div
				aria-hidden="true"
				className={cn(
					'text-muted-foreground border-border/60 hidden gap-x-4 border-b px-4 py-2 text-[11px] font-medium tracking-wide uppercase @3xl:grid',
					WIDE_COLS,
				)}
			>
				<span>Time</span>
				<span className="pl-6.5">Event</span>
				<span>{columns.actor}</span>
				<span>{columns.subject}</span>
				<span className="text-right">{columns.detail}</span>
				<span />
			</div>
			{children}
			{footer}
		</section>
	);
}

/** Rows bucketed under local-day headings (input newest-first). */
export function groupByDay<T>(
	items: T[],
	tsOf: (item: T) => number,
	now = Date.now(),
): { key: string; label: string; items: T[] }[] {
	const days: { key: string; label: string; items: T[] }[] = [];
	for (const item of items) {
		const ts = tsOf(item);
		const key = streamDayKey(ts);
		const last = days[days.length - 1];
		if (last && last.key === key) last.items.push(item);
		else days.push({ key, label: formatStreamDayLabel(ts, now), items: [item] });
	}
	return days;
}

/** A sticky day heading over that day's rows. */
export function LogDay({ label, children }: { label: string; children: ReactNode }) {
	return (
		<Fragment>
			<h3 className="bg-muted/85 text-muted-foreground border-border/60 sticky top-[var(--log-top,0px)] z-10 border-b px-3 py-1.5 text-[11px] font-semibold tracking-wide uppercase backdrop-blur sm:px-4">
				{label}
			</h3>
			<ul aria-label={label}>{children}</ul>
		</Fragment>
	);
}

/* ------------------------------------------------------------------ */
/* Stepping                                                            */
/* ------------------------------------------------------------------ */

function isTyping(target: EventTarget | null): boolean {
	const el = target as HTMLElement | null;
	if (!el) return false;
	return (
		el.isContentEditable ||
		['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName) ||
		el.closest('[role="dialog"], [role="alertdialog"], [role="listbox"]') != null
	);
}

/**
 * j / k (and ↓ / ↑) step the open record through the list; Esc closes it.
 * Only while the detail is docked — the modal sheet owns the keyboard.
 */
export function useLogStepping(
	listRef: RefObject<HTMLElement | null>,
	{ enabled, onClose }: { enabled: boolean; onClose: () => void },
) {
	useEffect(() => {
		if (!enabled) return;
		const onKeyDown = (e: globalThis.KeyboardEvent) => {
			if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
			if (isTyping(e.target)) return;
			if (e.key === 'Escape') {
				onClose();
				return;
			}
			const down = e.key === 'j' || e.key === 'ArrowDown';
			const up = e.key === 'k' || e.key === 'ArrowUp';
			if (!down && !up) return;
			const rows = [
				...(listRef.current?.querySelectorAll<HTMLElement>('[data-log-row]') ?? []),
			];
			if (rows.length === 0) return;
			const current = rows.findIndex(
				(r) => r.getAttribute('aria-current') === 'true' || r === document.activeElement,
			);
			const nextIndex =
				current < 0 ? 0 : Math.min(rows.length - 1, Math.max(0, current + (down ? 1 : -1)));
			if (nextIndex === current) return;
			e.preventDefault();
			const next = rows[nextIndex];
			next.focus({ preventScroll: true });
			next.scrollIntoView({ block: 'nearest' });
			next.click();
		};
		window.addEventListener('keydown', onKeyDown);
		return () => window.removeEventListener('keydown', onKeyDown);
	}, [enabled, listRef, onClose]);
}
