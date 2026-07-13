/**
 * MonitorList / MonitorRow — the shared "feed" presentation for the Monitor
 * list tabs (Executions, Jobs, Events, Audit).
 *
 * This replaces the flat <DataTable> grid for these surfaces with the richer
 * row vocabulary the Overview already uses (see TopOperations): a colour-tinted
 * status icon tile as a visual anchor, a strong title + muted subtitle identity
 * stack, optional inline meta badges, and a right-aligned trailing slot for
 * timing / actions. One component renders the same on phones and desktop
 * (it reflows with flexbox), so there's a single DOM tree — good for the a11y
 * tree and for tests — and the four tabs share one consistent, scannable look.
 */
import React from 'react';
import { LoadingState } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';

export type MonitorAccent = 'green' | 'orange' | 'pink' | 'blue' | 'neutral';

/** Tailwind tints per accent, mirroring the Overview's pill palette. */
const ACCENT_TILE: Record<MonitorAccent, string> = {
	green: 'bg-accent-green/12 text-accent-green',
	orange: 'bg-accent-orange/12 text-accent-orange',
	pink: 'bg-accent-pink/12 text-accent-pink',
	blue: 'bg-accent-blue/12 text-accent-blue',
	neutral: 'bg-muted text-muted-foreground',
};

export interface MonitorRowProps {
	/** Colour-tinted leading tile — a status/severity glyph anchoring the row. */
	icon: React.ReactNode;
	accent?: MonitorAccent;
	/** Primary line: the "what" (operation, job kind, event summary, action). */
	title: React.ReactNode;
	/** Secondary line: the "where/who" (host, job id, type, actor). */
	subtitle?: React.ReactNode;
	/** Optional error snippet — red-tinted, shown under the subtitle for
	 *  failed rows so the reason is visible without opening the detail sheet. */
	error?: string | null;
	/** Inline chips between identity and meta (status, HTTP, severity). */
	badges?: React.ReactNode;
	/** Right-aligned trailing column — timing, relative time, actions. */
	meta?: React.ReactNode;
	onClick?: () => void;
	/** Accessible name for a clickable row. */
	label?: string;
}

export function MonitorRow({
	icon,
	accent = 'neutral',
	title,
	subtitle,
	error,
	badges,
	meta,
	onClick,
	label,
}: MonitorRowProps) {
	const body = (
		<div className="flex items-center gap-3 px-3 py-3 sm:px-4">
			<span
				className={cn(
					'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg',
					ACCENT_TILE[accent],
				)}
				aria-hidden="true"
			>
				{icon}
			</span>

			<div className="flex min-w-0 flex-1 flex-col gap-0.5 sm:flex-row sm:items-center sm:gap-3">
				<div className="min-w-0 flex-1">
					<div className="text-foreground truncate text-sm font-medium">{title}</div>
					{subtitle != null && (
						<div className="text-muted-foreground mt-0.5 truncate text-xs">
							{subtitle}
						</div>
					)}
					{error && (
						<div className="text-danger mt-1 truncate text-xs" title={error}>
							{error}
						</div>
					)}
				</div>

				{badges != null && (
					<div className="flex shrink-0 flex-wrap items-center gap-1.5">{badges}</div>
				)}
			</div>

			{meta != null && (
				<div className="text-muted-foreground flex shrink-0 flex-col items-end gap-1 text-right text-xs whitespace-nowrap tabular-nums">
					{meta}
				</div>
			)}
		</div>
	);

	return (
		<li className="bg-card border-border/40 border-b last:border-0">
			{onClick ? (
				<button
					type="button"
					onClick={onClick}
					aria-label={label}
					className="hover:bg-muted focus-visible:bg-muted focus-visible:ring-ring block w-full text-left transition-colors outline-none focus-visible:ring-2 focus-visible:ring-inset"
				>
					{body}
				</button>
			) : (
				body
			)}
		</li>
	);
}

export interface MonitorListProps {
	title: string;
	/** Optional right-aligned header slot (count, live badge, etc.). */
	headerAction?: React.ReactNode;
	isLoading?: boolean;
	children: React.ReactNode;
	ariaLabel: string;
}

export function MonitorList({
	title,
	headerAction,
	isLoading,
	children,
	ariaLabel,
}: MonitorListProps) {
	return (
		<div className="border-border bg-card overflow-hidden rounded-xl border">
			<div className="border-border/60 flex items-center justify-between gap-2 border-b px-3 py-2.5 sm:px-4">
				<h2 className="text-muted-foreground text-[11px] font-semibold tracking-wider uppercase">
					{title}
				</h2>
				{headerAction}
			</div>
			{isLoading ? (
				<div className="p-4">
					<LoadingState />
				</div>
			) : (
				<ul aria-label={ariaLabel}>{children}</ul>
			)}
		</div>
	);
}
