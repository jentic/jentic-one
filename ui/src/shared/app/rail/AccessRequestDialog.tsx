/**
 * AccessRequestDialog — review and decide a filed access request.
 *
 * An `access_request.filed` event in the rail is a single row, but the request
 * behind it is an ENVELOPE of one or more line items the backend decides
 * INDEPENDENTLY (`POST /access-requests/{id}:decide` takes a per-item array).
 * Opening this dialog (from a rail row or the dashboard queue) shows every item
 * and lets the operator draft a per-item verdict, review it, and submit.
 *
 * The body re-expresses the jentic-webapp "resource-request" review flow on
 * jentic-one's shared/ui + design tokens, with one deliberate UX improvement:
 *   review  — an "Awaiting Decision" rail of pending item cards feeding into
 *             Approved / Denied chip lanes. Denying expands the reason field
 *             INLINE on the card (the webapp had no reason capture at all and
 *             buried nothing — here the "no, because…" is written exactly where
 *             the decision is made). An all-processed orange pulse nudges submit.
 *   confirm — a grouped summary of what will be approved / denied (with the
 *             reasons already captured) / skipped, plus a deviation warning when
 *             verdicts differ from what the agent asked for.
 *   done    — an inline success / declined / error terminal screen.
 *
 * Verdicts are DRAFT (client-side) until submit; jentic-one's `:decide` is
 * terminal server-side, so "undo" is purely local. Leftover pending items are
 * skipped on submit, matching the original webapp parity.
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
	AlertCircle,
	ArrowLeft,
	ArrowRight,
	CheckCircle2,
	KeyRound,
	MessageSquare,
	Shield,
	ShieldCheck,
	XCircle,
} from 'lucide-react';
import { Dialog } from '@/shared/ui/Dialog';
import { Button } from '@/shared/ui/Button';
import { Badge } from '@/shared/ui/Badge';
import { Textarea } from '@/shared/ui/Textarea';
import { LoadingState } from '@/shared/ui/LoadingState';
import { ErrorAlert } from '@/shared/ui/ErrorAlert';
import { ActorLabel } from '@/shared/ui/ActorLabel';
import { AgentBadge } from '@/shared/ui/AgentBadge';
import { AccessRequestItemCard } from '@/shared/app/rail/AccessRequestItemCard';
import { AccessRequestChip } from '@/shared/app/rail/AccessRequestChip';
import { OperationsSummary } from '@/shared/app/rail/OperationsSummary';
import { useActorDirectory } from '@/shared/hooks';
import {
	decideAccessRequest,
	getAccessRequest,
	itemTargetLabel,
	isScopeGrant,
	scopeLabel,
	parseItemRules,
	type AccessRequest,
	type AccessRequestItem,
	type ItemDecision,
} from '@/shared/lib/accessRequests';

type DraftStatus = 'pending' | 'denying' | 'approved' | 'denied';
type Step = 'review' | 'confirm';
type Outcome = 'granted' | 'declined' | 'error';

/** Per-item draft verdict (+ in-progress / final deny reason). */
type DraftState = Record<string, { status: DraftStatus; reason: string }>;

function statusVariant(status: string): 'success' | 'danger' | 'pending' | 'default' {
	if (status === 'approved' || status === 'partially_approved') return 'success';
	if (status === 'denied') return 'danger';
	if (status === 'pending') return 'pending';
	return 'default';
}

/** Tiny uppercase mono eyebrow — the house section-label style. */
function Eyebrow({ children }: { children: ReactNode }) {
	return (
		<p className="text-muted-foreground font-mono text-[10px] font-medium tracking-widest uppercase">
			{children}
		</p>
	);
}

export type AccessRequestDialogProps = {
	/** The access-request id (from a filed event's `access_request_id` token, or a queue row). */
	requestId: string | null;
	/**
	 * The filed event id, passed back to `onResolved` after a successful submit.
	 * Null when opened from a non-event surface (e.g. the dashboard queue), in
	 * which case `onResolved` is simply not called.
	 */
	eventId?: string | null;
	open: boolean;
	onClose: () => void;
	/** Called with the filed event id after a successful submit so an event-driven parent (the rail) can settle its row. */
	onResolved?: (eventId: string) => void;
	/** Called after a successful decision regardless of event linkage (e.g. the dashboard card refreshes its query). */
	onDecided?: () => void;
};

export function AccessRequestDialog({
	requestId,
	eventId,
	open,
	onClose,
	onResolved,
	onDecided,
}: AccessRequestDialogProps) {
	const [request, setRequest] = useState<AccessRequest | null>(null);
	const [drafts, setDrafts] = useState<DraftState>({});
	const [step, setStep] = useState<Step>('review');
	const [outcome, setOutcome] = useState<Outcome | null>(null);
	const [loading, setLoading] = useState(false);
	const [submitting, setSubmitting] = useState(false);
	const [error, setError] = useState<string | null>(null);
	// Items the operator APPROVED but the server could not fulfill (it denies them
	// with a reason — e.g. "No toolkit serves API … provision and bind a
	// credential first"). The server's `:decide` response is authoritative, so we
	// surface these instead of falsely reporting "Access granted".
	const [blocked, setBlocked] = useState<{ label: string; reason: string }[]>([]);
	const { resolve: resolveActor } = useActorDirectory();

	// Load (and reset) whenever the dialog opens for a request. Seed a `pending`
	// draft for every still-pending server item; already-decided items render
	// read-only and are never re-submitted.
	useEffect(() => {
		if (!open || !requestId) return;
		let cancelled = false;
		setLoading(true);
		setError(null);
		setRequest(null);
		setDrafts({});
		setStep('review');
		setOutcome(null);
		setBlocked([]);
		void (async () => {
			try {
				const ar = await getAccessRequest(requestId);
				if (cancelled) return;
				setRequest(ar);
				const seed: DraftState = {};
				for (const item of ar.items) {
					if (item.status === 'pending')
						seed[item.id] = { status: 'pending', reason: '' };
				}
				setDrafts(seed);
			} catch (e) {
				if (cancelled) return;
				setError(e instanceof Error ? e.message : 'Failed to load the access request.');
			} finally {
				if (!cancelled) setLoading(false);
			}
		})();
		return () => {
			cancelled = true;
		};
	}, [open, requestId]);

	const itemsById = useMemo(() => {
		const map = new Map<string, AccessRequestItem>();
		for (const item of request?.items ?? []) map.set(item.id, item);
		return map;
	}, [request]);

	const partition = useMemo(() => {
		const pending: AccessRequestItem[] = [];
		const approved: AccessRequestItem[] = [];
		const denied: AccessRequestItem[] = [];
		for (const [id, draft] of Object.entries(drafts)) {
			const item = itemsById.get(id);
			if (!item) continue;
			if (draft.status === 'approved') approved.push(item);
			else if (draft.status === 'denied') denied.push(item);
			else pending.push(item); // pending + denying both live in the rail
		}
		return { pending, approved, denied };
	}, [drafts, itemsById]);

	const totalDraftable = Object.keys(drafts).length;
	const decidedCount = partition.approved.length + partition.denied.length;
	const allProcessed = totalDraftable > 0 && partition.pending.length === 0;
	const hasAnyDecision = decidedCount > 0;
	// Already-decided items the operator can't act on (rendered read-only).
	const decidedItems = useMemo(
		() => (request?.items ?? []).filter((i) => i.status !== 'pending'),
		[request],
	);

	const approve = useCallback((id: string) => {
		setDrafts((prev) =>
			id in prev ? { ...prev, [id]: { status: 'approved', reason: '' } } : prev,
		);
	}, []);
	// Deny is two-phase: the first click opens the inline reason field
	// (`denying`); the confirm click (with a non-empty reason) finalises to
	// `denied`. Calling this again while already `denying` finalises it.
	const startOrConfirmDeny = useCallback((id: string) => {
		setDrafts((prev) => {
			const cur = prev[id];
			if (!cur) return prev;
			if (cur.status === 'denying') {
				if (cur.reason.trim().length === 0) return prev; // guard: needs a reason
				return { ...prev, [id]: { status: 'denied', reason: cur.reason } };
			}
			return { ...prev, [id]: { status: 'denying', reason: cur.reason } };
		});
	}, []);
	const cancelDeny = useCallback((id: string) => {
		setDrafts((prev) =>
			prev[id]?.status === 'denying'
				? { ...prev, [id]: { status: 'pending', reason: prev[id].reason } }
				: prev,
		);
	}, []);
	const undo = useCallback((id: string) => {
		setDrafts((prev) =>
			id in prev ? { ...prev, [id]: { status: 'pending', reason: '' } } : prev,
		);
	}, []);
	const setReason = useCallback((id: string, reason: string) => {
		setDrafts((prev) => (prev[id] ? { ...prev, [id]: { ...prev[id], reason } } : prev));
	}, []);
	const decideAll = useCallback((verdict: 'approved' | 'denied') => {
		setDrafts((prev) => {
			const next: DraftState = {};
			for (const [id, draft] of Object.entries(prev)) {
				next[id] =
					verdict === 'denied'
						? { status: 'denied', reason: draft.reason }
						: { status: 'approved', reason: '' };
			}
			return next;
		});
	}, []);

	// A denied item with an empty reason blocks submit (shouldn't happen via the
	// inline flow, but "Deny all" can produce reasonless denials).
	const missingReason = partition.denied.some(
		(item) => (drafts[item.id]?.reason ?? '').trim().length === 0,
	);

	// When the operator lands on confirm with reasonless denials (e.g. after
	// "Deny all"), pull focus to the first empty reason field so it's obvious
	// what's blocking submit. This fires ONCE on entering the confirm step — not
	// on every `drafts`/`missingReason` change — otherwise each keystroke would
	// re-run it and yank focus back to the first empty field mid-typing.
	useEffect(() => {
		if (step !== 'confirm') return;
		const first = partition.denied.find(
			(item) => (drafts[item.id]?.reason ?? '').trim().length === 0,
		);
		if (!first) return;
		const el = document.getElementById(`ar-confirm-reason-${first.id}`);
		if (el instanceof HTMLTextAreaElement) {
			el.focus();
			el.scrollIntoView({ block: 'center', behavior: 'smooth' });
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [step]);

	async function submit() {
		if (!request || !hasAnyDecision || missingReason) return;
		setSubmitting(true);
		setError(null);
		const decisions: ItemDecision[] = [];
		for (const [id, draft] of Object.entries(drafts)) {
			if (draft.status === 'approved') {
				decisions.push({ item_id: id, decision: 'approved', decision_reason: null });
			} else if (draft.status === 'denied') {
				decisions.push({
					item_id: id,
					decision: 'denied',
					decision_reason: draft.reason.trim() || null,
				});
			}
		}
		try {
			const updated = await decideAccessRequest(request.id, decisions);
			if (eventId) onResolved?.(eventId);
			onDecided?.();

			// The server is authoritative and can override an "approved" verdict to
			// "denied" when the target can't be fulfilled as filed (e.g. a
			// toolkit.bind whose toolkit/credential doesn't exist yet). Read the
			// returned items rather than trusting our own draft, so the terminal
			// screen reflects what actually happened — and carries the reason back.
			const actedIds = new Set(decisions.map((d) => d.item_id));
			const approvedIds = new Set(
				decisions.filter((d) => d.decision === 'approved').map((d) => d.item_id),
			);
			const itemsById = new Map(updated.items.map((it) => [it.id, it]));

			const blockedItems = updated.items.filter(
				(it) => approvedIds.has(it.id) && it.status === 'denied',
			);
			setBlocked(
				blockedItems.map((it) => ({
					label: isScopeGrant(it) ? scopeLabel(it) : itemTargetLabel(it),
					reason:
						it.decision_reason?.trim() ||
						'The platform could not fulfill this request as filed.',
				})),
			);

			const anyGranted = [...actedIds].some((id) => itemsById.get(id)?.status === 'approved');
			setOutcome(anyGranted ? 'granted' : 'declined');
		} catch (e) {
			setError(e instanceof Error ? e.message : 'Failed to record the decision.');
			setOutcome('error');
		} finally {
			setSubmitting(false);
		}
	}

	function renderFooter() {
		if (loading || error || !request) {
			return (
				<Button variant="ghost" onClick={onClose}>
					Close
				</Button>
			);
		}
		if (step === 'confirm') {
			return (
				<>
					<Button
						variant="secondary"
						onClick={() => setStep('review')}
						disabled={submitting}
						className="mr-auto"
					>
						<ArrowLeft className="mr-1 h-4 w-4" aria-hidden="true" />
						Back
					</Button>
					<Button variant="ghost" onClick={onClose} disabled={submitting}>
						Cancel
					</Button>
					<Button
						onClick={() => void submit()}
						loading={submitting}
						disabled={missingReason}
					>
						{submitting ? (
							'Submitting…'
						) : (
							<>
								<ShieldCheck className="mr-1 h-4 w-4" aria-hidden="true" />
								Confirm decision
							</>
						)}
					</Button>
				</>
			);
		}
		return (
			<>
				<Button variant="ghost" onClick={onClose}>
					Cancel
				</Button>
				<Button onClick={() => setStep('confirm')} disabled={!hasAnyDecision}>
					Review &amp; submit{decidedCount > 0 ? ` (${decidedCount})` : ''}
					<ArrowRight className="ml-1 h-4 w-4" aria-hidden="true" />
				</Button>
			</>
		);
	}

	function renderReview() {
		return (
			<div className="text-foreground space-y-5">
				{/* Agent banner — who is asking, and why. */}
				<div className="border-accent-blue/20 bg-accent-blue/5 flex items-start gap-3 rounded-xl border p-3.5">
					<AgentBadge
						id={request?.actor_id}
						name={
							(request?.actor_id && resolveActor(request.actor_id)) ||
							request?.actor_id
						}
						kind="Agent"
						size="md"
					/>
					<div className="min-w-0 flex-1">
						<p className="text-sm font-semibold">Agent is requesting access</p>
						{request?.actor_id && (
							<ActorLabel
								actorId={request.actor_id}
								className="text-muted-foreground block truncate text-xs"
							/>
						)}
						{request?.reason && (
							<div className="bg-muted/50 mt-2 flex items-baseline gap-2 rounded-md px-2.5 py-1.5">
								<span className="text-muted-foreground flex shrink-0 items-center gap-1 text-[10px] font-medium tracking-wide uppercase">
									<MessageSquare className="h-3 w-3" aria-hidden="true" />
									Reason
								</span>
								<p className="text-foreground text-xs">
									&ldquo;{request.reason}&rdquo;
								</p>
							</div>
						)}
					</div>
				</div>

				{decidedItems.length > 0 && (
					<section className="space-y-2">
						<Eyebrow>Already decided</Eyebrow>
						<ul className="space-y-1">
							{decidedItems.map((item) => (
								<li
									key={item.id}
									className="border-border bg-muted/40 flex items-center justify-between rounded-md border px-3 py-2 text-sm"
								>
									<span className="truncate">{itemTargetLabel(item)}</span>
									<Badge variant={statusVariant(item.status)}>
										{item.status}
									</Badge>
								</li>
							))}
						</ul>
					</section>
				)}

				{totalDraftable === 0 ? (
					<p className="text-muted-foreground py-8 text-center text-sm">
						There are no pending items left to decide on this request.
					</p>
				) : (
					<div className="space-y-5">
						{/* Awaiting Decision rail */}
						<section className="space-y-2">
							<div className="flex items-center gap-2">
								<span className="bg-accent-orange/15 flex h-5 w-5 items-center justify-center rounded-full">
									<Shield
										className="text-accent-orange h-3 w-3"
										aria-hidden="true"
									/>
								</span>
								<h3 className="text-sm font-semibold">Awaiting Decision</h3>
								<motion.span
									key={`pending-${partition.pending.length}`}
									className="bg-accent-orange/15 text-accent-orange rounded-full px-2 py-0.5 text-xs font-medium tabular-nums"
									initial={{ scale: 0.85 }}
									animate={{ scale: 1 }}
									transition={{ type: 'spring', stiffness: 500, damping: 28 }}
									aria-live="polite"
									aria-label={`${partition.pending.length} item${
										partition.pending.length === 1 ? '' : 's'
									} awaiting decision`}
								>
									{partition.pending.length}
								</motion.span>
							</div>
							{partition.pending.length > 0 ? (
								<div
									className="-mx-1 px-1 pb-2 sm:overflow-x-auto"
									role="group"
									aria-label="Items awaiting decision"
									tabIndex={0}
								>
									<div
										className="flex flex-col gap-3 sm:flex-row"
										style={{ minWidth: 'min-content' }}
									>
										<AnimatePresence initial={false} mode="popLayout">
											{partition.pending.map((item) => {
												const draft = drafts[item.id];
												return (
													<AccessRequestItemCard
														key={item.id}
														item={item}
														denying={draft?.status === 'denying'}
														reason={draft?.reason ?? ''}
														onApprove={() => approve(item.id)}
														onStartDeny={() =>
															startOrConfirmDeny(item.id)
														}
														onCancelDeny={() => cancelDeny(item.id)}
														onReasonChange={(r) =>
															setReason(item.id, r)
														}
													/>
												);
											})}
										</AnimatePresence>
									</div>
								</div>
							) : (
								<motion.div
									initial={{ opacity: 0, y: 6 }}
									animate={{ opacity: 1, y: 0 }}
									className="border-accent-green/30 bg-accent-green/5 flex flex-col items-center rounded-xl border border-dashed px-4 py-6 text-center"
								>
									<CheckCircle2
										className="text-accent-green mb-1.5 h-6 w-6"
										aria-hidden="true"
									/>
									<p className="text-accent-green text-sm font-medium">
										All items processed
									</p>
									<p className="text-muted-foreground text-xs">
										Review &amp; submit when ready
									</p>
								</motion.div>
							)}
						</section>

						{/* Approved / Denied lanes */}
						<div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
							<section className="space-y-1.5">
								<div className="text-accent-green flex items-center gap-1.5 text-xs font-medium">
									<CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
									Approved ({partition.approved.length})
								</div>
								<div className="border-accent-green/20 bg-accent-green/5 flex min-h-[44px] flex-wrap gap-1.5 rounded-lg border-2 border-dashed p-2">
									<AnimatePresence initial={false} mode="popLayout">
										{partition.approved.map((item) => (
											<AccessRequestChip
												key={item.id}
												item={item}
												verdict="approved"
												onUndo={() => undo(item.id)}
											/>
										))}
									</AnimatePresence>
									{partition.approved.length === 0 && (
										<span className="text-muted-foreground/70 self-center px-1 text-xs">
											None yet
										</span>
									)}
								</div>
							</section>
							<section className="space-y-1.5">
								<div className="text-muted-foreground flex items-center gap-1.5 text-xs font-medium">
									<XCircle className="h-3.5 w-3.5" aria-hidden="true" />
									Denied ({partition.denied.length})
								</div>
								<div className="border-border bg-muted/20 flex min-h-[44px] flex-wrap gap-1.5 rounded-lg border-2 border-dashed p-2">
									<AnimatePresence initial={false} mode="popLayout">
										{partition.denied.map((item) => (
											<AccessRequestChip
												key={item.id}
												item={item}
												verdict="denied"
												reason={drafts[item.id]?.reason}
												onUndo={() => undo(item.id)}
											/>
										))}
									</AnimatePresence>
									{partition.denied.length === 0 && (
										<span className="text-muted-foreground/70 self-center px-1 text-xs">
											None
										</span>
									)}
								</div>
							</section>
						</div>

						{totalDraftable > 1 && (
							<div className="flex flex-wrap items-center gap-2">
								<Eyebrow>Decide all</Eyebrow>
								<Button
									variant="outline"
									size="sm"
									onClick={() => decideAll('approved')}
									className="h-9 px-3 text-xs sm:h-7 sm:px-2.5"
								>
									Approve all
								</Button>
								<Button
									variant="secondary"
									size="sm"
									onClick={() => decideAll('denied')}
									className="h-9 px-3 text-xs sm:h-7 sm:px-2.5"
								>
									Deny all
								</Button>
							</div>
						)}

						<AnimatePresence>
							{allProcessed && (
								<motion.div
									initial={{ opacity: 0 }}
									animate={{
										opacity: 1,
										boxShadow: [
											'0 0 0 2px hsl(var(--accent-orange) / 0.6)',
											'0 0 0 2px hsl(var(--accent-orange) / 0.6)',
											'0 0 0 0 transparent',
										],
									}}
									exit={{ opacity: 0 }}
									transition={{ duration: 1.4, times: [0, 0.6, 1] }}
									className="border-accent-orange/30 bg-accent-orange/10 text-accent-orange rounded-lg border px-3 py-2.5 text-center text-xs font-medium"
									role="status"
								>
									Every item is decided — continue to review &amp; submit.
								</motion.div>
							)}
						</AnimatePresence>
					</div>
				)}
			</div>
		);
	}

	function renderConfirm() {
		const willApprove = partition.approved;
		const willDeny = partition.denied;
		const willSkip = partition.pending;
		const hasDeviation = willDeny.length > 0 || willSkip.length > 0;
		return (
			<div className="text-foreground space-y-5">
				{hasDeviation && (
					<div className="border-accent-orange/40 bg-accent-orange/10 flex gap-3 rounded-lg border p-3.5">
						<Shield
							className="text-accent-orange mt-0.5 h-5 w-5 shrink-0"
							aria-hidden="true"
						/>
						<div className="text-sm">
							<p className="text-accent-orange font-medium">
								This differs from what was requested
							</p>
							<ul className="text-muted-foreground mt-1 space-y-0.5 text-xs">
								{willDeny.length > 0 && (
									<li>
										&bull; {willDeny.length} requested item(s) will be denied
									</li>
								)}
								{willSkip.length > 0 && (
									<li>
										&bull; {willSkip.length} item(s) left undecided (skipped)
									</li>
								)}
							</ul>
						</div>
					</div>
				)}

				{willApprove.length > 0 && (
					<section className="space-y-2">
						<div className="text-accent-green flex items-center gap-1.5 text-sm font-semibold">
							<CheckCircle2 className="h-4 w-4" aria-hidden="true" />
							Will be approved ({willApprove.length})
						</div>
						<ul className="space-y-1.5">
							{willApprove.map((item) => {
								const scope = isScopeGrant(item);
								const rules = scope ? [] : parseItemRules(item);
								return (
									<li
										key={item.id}
										className="border-accent-green/20 bg-accent-green/5 rounded-md border px-3 py-2 text-sm break-words"
									>
										<div className="flex flex-wrap items-center gap-1.5">
											<span className="font-medium">
												{scope ? scopeLabel(item) : itemTargetLabel(item)}
											</span>
											{scope && (
												<span className="bg-accent-yellow/10 text-accent-yellow inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium">
													<KeyRound
														className="h-3 w-3"
														aria-hidden="true"
													/>
													Platform scope
												</span>
											)}
										</div>
										{rules.length > 0 && (
											<OperationsSummary
												rules={rules}
												targetLabel={itemTargetLabel(item)}
											/>
										)}
									</li>
								);
							})}
						</ul>
					</section>
				)}

				{willDeny.length > 0 && (
					<section className="space-y-2">
						<div className="text-muted-foreground flex items-center gap-1.5 text-sm font-semibold">
							<XCircle className="h-4 w-4" aria-hidden="true" />
							Will be denied ({willDeny.length})
						</div>
						<ul className="space-y-1.5">
							{willDeny.map((item) => {
								const scope = isScopeGrant(item);
								const rules = scope ? [] : parseItemRules(item);
								return (
									<li
										key={item.id}
										className="border-border bg-muted/30 rounded-md border px-3 py-2 text-sm"
									>
										<div className="flex flex-wrap items-center gap-1.5">
											<span className="decoration-muted-foreground/40 font-medium break-words line-through">
												{scope ? scopeLabel(item) : itemTargetLabel(item)}
											</span>
											{scope && (
												<span className="bg-accent-yellow/10 text-accent-yellow inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium">
													<KeyRound
														className="h-3 w-3"
														aria-hidden="true"
													/>
													Platform scope
												</span>
											)}
										</div>
										{/* Mirror step 1: show WHAT was requested even when
										    denying, so a fast "deny all" stays traceable —
										    the operator can see exactly what they turned
										    down before submitting. */}
										{rules.length > 0 && (
											<OperationsSummary
												rules={rules}
												targetLabel={itemTargetLabel(item)}
											/>
										)}
										{/* Reason is ALWAYS an editable field here — never a
										    read-only preview. A previous version swapped the
										    textarea for a static <p> the moment the reason
										    became non-empty, which unmounted the input on the
										    first keystroke and made reasonless "Deny all"
										    items impossible to caption (and thus the whole
										    request unsubmittable, since `missingReason` keeps
										    Confirm disabled). Keeping it mounted lets the
										    operator type freely AND refine reasons that were
										    captured inline on the card in step 1. */}
										<div className="mt-2">
											<label
												htmlFor={`ar-confirm-reason-${item.id}`}
												className="text-muted-foreground mb-1 flex items-center gap-1 text-[10px] font-medium tracking-wide uppercase"
											>
												<MessageSquare
													className="h-2.5 w-2.5"
													aria-hidden="true"
												/>
												Reason (sent back to the agent)
											</label>
											<Textarea
												id={`ar-confirm-reason-${item.id}`}
												rows={2}
												resizable="none"
												value={drafts[item.id]?.reason ?? ''}
												onChange={(e) => setReason(item.id, e.target.value)}
												placeholder="Why is this being denied?"
												className="text-xs"
											/>
										</div>
									</li>
								);
							})}
						</ul>
					</section>
				)}

				{willSkip.length > 0 && (
					<section className="space-y-1">
						<Eyebrow>Left undecided ({willSkip.length})</Eyebrow>
						<p className="text-muted-foreground text-xs">
							These stay pending and can be decided later.
						</p>
					</section>
				)}

				{missingReason && (
					<p className="text-accent-orange flex items-center gap-1.5 text-xs font-medium">
						<AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
						Add a reason for each denied item to continue.
					</p>
				)}
			</div>
		);
	}

	// Terminal screen (success / declined / error) replaces the body once submitted.
	if (outcome) {
		const hasBlocked = blocked.length > 0;
		// A "declined" outcome with blocked items isn't a human "no" — it's the
		// platform refusing to fulfill what the operator approved. Say so plainly
		// (with the reason) instead of the misleading "Request declined".
		const isFulfillmentBlock = outcome === 'declined' && hasBlocked;
		const copy =
			outcome === 'granted'
				? {
						title: 'Access granted',
						subtitle: hasBlocked
							? 'Some items were granted, but others could not be fulfilled — see below.'
							: 'The agent can now use what was approved.',
					}
				: isFulfillmentBlock
					? {
							title: 'Could not grant access',
							subtitle:
								'You approved this, but the platform could not fulfill it as filed:',
						}
					: outcome === 'declined'
						? {
								title: 'Request declined',
								subtitle: 'No access was granted to the agent.',
							}
						: {
								title: 'Something went wrong',
								subtitle: error ?? 'The decision could not be recorded.',
							};
		const icon =
			outcome === 'granted' ? (
				<CheckCircle2 className="text-accent-green h-10 w-10" aria-hidden="true" />
			) : isFulfillmentBlock ? (
				<AlertCircle className="text-accent-orange h-10 w-10" aria-hidden="true" />
			) : outcome === 'declined' ? (
				<XCircle className="text-muted-foreground h-10 w-10" aria-hidden="true" />
			) : (
				<AlertCircle className="text-danger h-10 w-10" aria-hidden="true" />
			);
		const disc =
			outcome === 'granted'
				? 'bg-accent-green/10'
				: isFulfillmentBlock
					? 'bg-accent-orange/10'
					: outcome === 'error'
						? 'bg-danger/10'
						: 'bg-muted';
		return (
			<Dialog
				open={open}
				onClose={onClose}
				title="Access request"
				size="md"
				footer={
					<>
						{outcome === 'error' && (
							<Button variant="secondary" onClick={() => setOutcome(null)}>
								Try again
							</Button>
						)}
						<Button onClick={onClose}>Done</Button>
					</>
				}
			>
				<div className="flex min-h-[220px] items-center justify-center" role="status">
					<motion.div
						className="text-center"
						initial={{ opacity: 0, scale: 0.9 }}
						animate={{ opacity: 1, scale: 1 }}
					>
						<div
							className={`mx-auto flex h-20 w-20 items-center justify-center rounded-full ${disc}`}
						>
							{icon}
						</div>
						<h2 className="text-foreground mt-6 text-2xl font-semibold">
							{copy.title}
						</h2>
						<p className="text-muted-foreground mt-2">{copy.subtitle}</p>
						{hasBlocked && (
							<ul className="mx-auto mt-4 max-w-md space-y-2 text-left">
								{blocked.map((b, i) => (
									<li
										key={`${b.label}-${i}`}
										className="border-accent-orange/30 bg-accent-orange/5 rounded-lg border px-3 py-2"
									>
										<p className="text-foreground text-sm font-medium break-words">
											{b.label}
										</p>
										<p className="text-muted-foreground mt-0.5 text-xs break-words">
											{b.reason}
										</p>
									</li>
								))}
							</ul>
						)}
					</motion.div>
				</div>
			</Dialog>
		);
	}

	return (
		<Dialog
			open={open}
			onClose={onClose}
			title="Access request"
			subtitle={
				step === 'confirm'
					? 'Step 2 of 2 · Confirm decision'
					: !loading && !error && request
						? 'Step 1 of 2 · Review each item'
						: undefined
			}
			size="xl"
			footer={renderFooter()}
		>
			{loading && <LoadingState message="Loading the access request…" />}
			{!loading && error && <ErrorAlert message={error} />}
			{!loading &&
				!error &&
				request &&
				(step === 'review' ? renderReview() : renderConfirm())}
		</Dialog>
	);
}
