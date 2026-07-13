/**
 * RailEventRow — single platform event in the rail feed.
 *
 * Density model (driven by real `EventSeverity`):
 *   • critical / error: 3-line layout, tinted background, action slot
 *   • warning:          3-line layout, no tint
 *   • info:             1-line compact
 *
 * Exception: an unacknowledged event that `requiresAction` always uses the full
 * layout regardless of severity, so its inline action slot is never hidden
 * (real `access_request.filed` events are INFO severity — see issue #652).
 *
 * Inline-action slot:
 *   • "View" — opens the access-request detail dialog (per-item approve/deny)
 *     for a filed `access_request.filed` event
 *   • "Deny" — real `POST /access-requests/{id}:decide` (reason-gated fast path:
 *     clicking it reveals an inline note field before it fires, denying the
 *     whole request)
 *   • "Acknowledge" — real `PATCH /events/{id}` via the parent
 *   • "View …" — pure-navigation deep-links into the execution/job/trace
 *
 * Acknowledged events collapse to the compact 1-line variant regardless of
 * severity, so the row visually fades once the operator has handled it.
 */
import type React from 'react';
import { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { StreamEventIcon } from '@/shared/app/rail/StreamEventIcon';
import { DenyReasonField } from '@/shared/app/rail/DenyReasonField';
import {
	formatStreamTime,
	inlineActionsFor,
	primaryDestinationFor,
	severityStripeClass,
	STREAM_KIND_LABEL,
} from '@/shared/lib/agentStream';
import type { InlineActionSpec, StreamEvent } from '@/shared/lib/agentStream';
import { cn } from '@/shared/lib/utils';

export type RailEventRowProps = {
	ev: StreamEvent;
	groupCount?: number; // > 1 means this row represents a collapsed group
	expanded?: boolean;
	onToggleExpand?: () => void;
	onAction?: (eventId: string, action: InlineActionSpec, reason?: string) => void;
	/** Open the access-request detail dialog for a filed event's "View" action. */
	onOpenRequest?: (requestId: string, eventId: string) => void;
	onNavigate?: (href: string) => void;
};

const KIND_LABEL = STREAM_KIND_LABEL;

function isCompactSeverity(ev: StreamEvent): boolean {
	if (ev.acknowledged) return true;
	// An unacknowledged event that still needs a human decision must keep its
	// full layout so the inline action slot (View/Deny/Acknowledge) renders.
	// Real `access_request.filed` events are emitted at INFO severity, so gating
	// compactness on severity alone hid their View/Deny buttons — they only
	// showed under MSW because the mock seeded them at `warning`. See issue #652.
	if (ev.requiresAction) return false;
	return ev.severity === 'info';
}

export function RailEventRow({
	ev,
	groupCount = 1,
	expanded = false,
	onToggleExpand,
	onAction,
	onOpenRequest,
	onNavigate,
}: RailEventRowProps) {
	const compact = isCompactSeverity(ev);
	const isCritical = (ev.severity === 'critical' || ev.severity === 'error') && !ev.acknowledged;
	const actions = inlineActionsFor(ev);
	// When the operator clicks a reason-gated action (Deny), we reveal an inline
	// note field and hold the action until they confirm.
	const [pendingReasonFor, setPendingReasonFor] = useState<InlineActionSpec | null>(null);
	const [reason, setReason] = useState('');
	const headline = KIND_LABEL[ev.kind];
	const submeta = [ev.tokens.toolkit_id, ev.meta].filter(Boolean).join(' · ');
	const dest = onNavigate ? primaryDestinationFor(ev) : null;
	const navProps = dest
		? {
				role: 'link' as const,
				tabIndex: 0,
				onClick: (e: React.MouseEvent) => {
					if ((e.target as HTMLElement).closest('button, [role="button"]')) return;
					onNavigate?.(dest);
				},
				onKeyDown: (e: React.KeyboardEvent) => {
					if (e.key === 'Enter' || e.key === ' ') {
						e.preventDefault();
						onNavigate?.(dest);
					}
				},
				title: `Open ${dest}`,
				'aria-label': `${headline}: ${ev.title}. Open detail.`,
			}
		: {};

	if (compact) {
		return (
			<div
				{...navProps}
				className={cn(
					'flex items-center gap-2 border-l-2 px-2 py-1',
					severityStripeClass(ev.severity),
					ev.acknowledged && 'opacity-60',
					dest && 'hover:bg-background/40 cursor-pointer',
				)}
			>
				<StreamEventIcon ev={ev} />
				<span className="text-muted-foreground min-w-0 flex-1 truncate text-[11px]">
					<span className="text-foreground/90 font-semibold">{headline}</span> {ev.title}
					{groupCount > 1 && (
						<span className="text-primary ml-1 font-mono text-[10px]">
							×{groupCount}
						</span>
					)}
				</span>
				{ev.acknowledged && (
					<span className="text-success shrink-0 font-mono text-[10px] tracking-wider uppercase">
						Acked
					</span>
				)}
				<span className="text-muted-foreground shrink-0 font-mono text-[10px]">
					{formatStreamTime(ev.tsMs)}
				</span>
			</div>
		);
	}

	return (
		<div
			{...navProps}
			className={cn(
				'flex gap-2 border-l-2 px-2 py-1.5',
				severityStripeClass(ev.severity),
				isCritical && 'bg-danger/5',
				dest && 'hover:bg-background/40 cursor-pointer',
			)}
		>
			<StreamEventIcon ev={ev} className="mt-0.5" />
			<div className="min-w-0 flex-1">
				<div className="flex items-center gap-1.5">
					<span className="text-foreground truncate text-xs font-semibold">
						{headline}
					</span>
					{groupCount > 1 && (
						<button
							type="button"
							onClick={onToggleExpand}
							className="text-primary hover:text-primary-hover shrink-0 rounded-full px-1.5 py-0 font-mono text-[10px] font-semibold tracking-wider tabular-nums"
							aria-label={expanded ? 'Collapse group' : 'Expand group'}
						>
							×{groupCount}{' '}
							{expanded ? (
								<ChevronUp className="inline h-2.5 w-2.5" />
							) : (
								<ChevronDown className="inline h-2.5 w-2.5" />
							)}
						</button>
					)}
					<span className="text-muted-foreground ml-auto shrink-0 font-mono text-[10px]">
						{formatStreamTime(ev.tsMs)}
					</span>
				</div>
				<div className="mt-0.5 flex items-center gap-1.5">
					<span className="text-muted-foreground truncate text-[11px]">{ev.title}</span>
				</div>
				{submeta && (
					<div className="text-muted-foreground mt-0.5 truncate font-mono text-[10px]">
						{submeta}
					</div>
				)}
				{actions.length > 0 && (onAction || onOpenRequest) && (
					<div className="mt-1.5 flex flex-col gap-1.5">
						<div className="flex flex-wrap gap-1">
							{actions.map((action) => {
								const tone =
									action.kind === 'acknowledge' ||
									action.kind === 'approve' ||
									action.kind === 'view_request'
										? 'primary'
										: action.kind === 'deny'
											? 'danger'
											: 'ghost';
								return (
									<Button
										key={action.kind}
										variant={tone}
										size="sm"
										onClick={() => {
											if (action.opensRequest) {
												const reqId = ev.tokens.access_request_id;
												if (reqId) onOpenRequest?.(reqId, ev.id);
												return;
											}
											if (action.requiresReason) {
												setReason('');
												setPendingReasonFor(
													pendingReasonFor?.kind === action.kind
														? null
														: action,
												);
												return;
											}
											onAction?.(ev.id, action);
										}}
										className="h-6 px-2 text-[11px]"
										aria-expanded={
											action.requiresReason
												? pendingReasonFor?.kind === action.kind
												: undefined
										}
									>
										{action.label}
									</Button>
								);
							})}
						</div>
						{pendingReasonFor && (
							<div className="flex flex-col gap-1">
								<DenyReasonField
									id={`deny-reason-${ev.id}`}
									value={reason}
									onChange={setReason}
									autoFocus
								/>
								<div className="flex gap-1">
									<Button
										variant="danger"
										size="sm"
										disabled={reason.trim().length === 0}
										onClick={() => {
											const action = pendingReasonFor;
											setPendingReasonFor(null);
											onAction?.(ev.id, action, reason.trim());
											setReason('');
										}}
										className="h-6 px-2 text-[11px]"
									>
										Confirm {pendingReasonFor.label.toLowerCase()}
									</Button>
									<Button
										variant="ghost"
										size="sm"
										onClick={() => {
											setPendingReasonFor(null);
											setReason('');
										}}
										className="h-6 px-2 text-[11px]"
									>
										Cancel
									</Button>
								</div>
							</div>
						)}
					</div>
				)}
			</div>
		</div>
	);
}
