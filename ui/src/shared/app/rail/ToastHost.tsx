/**
 * ToastHost — transient toast surface driven by the live agent stream. Pops the
 * latest stream event as a toast when it matches the operator's chosen scope,
 * auto-dismisses after a TTL, and is pinned to the bottom-right (matching the
 * platform toaster) — shifted left of the rail at `xl+` so the two never
 * overlap. Mounted by the shell alongside `AgentRail`.
 */
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { X } from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { StreamEventIcon } from '@/shared/app/rail/StreamEventIcon';
import {
	formatStreamTime,
	inlineActionsFor,
	matchesToastScope,
	primaryDestinationFor,
	RAIL_COLLAPSE_CHANGE_EVENT,
	RAIL_COLLAPSED_STORAGE_KEY,
	readToastScope,
	severityStripeClass,
	STREAM_KIND_LABEL,
	TOAST_SCOPE_CHANGE_EVENT,
	TOAST_SCOPE_STORAGE_KEY,
	useAgentStream,
} from '@/shared/lib/agentStream';
import type { InlineActionSpec, StreamEvent, ToastScope } from '@/shared/lib/agentStream';
import { cn } from '@/shared/lib/utils';

function readRailCollapsed(): boolean {
	if (typeof window === 'undefined') return false;
	try {
		return window.localStorage.getItem(RAIL_COLLAPSED_STORAGE_KEY) === '1';
	} catch {
		return false;
	}
}

const TOAST_TTL_MS = 6000;
const MAX_TOASTS = 3;

const KIND_LABEL = STREAM_KIND_LABEL;

type Toast = StreamEvent & { addedAt: number };

export function ToastHost() {
	const { latest, acknowledge } = useAgentStream();
	const navigate = useNavigate();
	const [toasts, setToasts] = useState<Toast[]>([]);
	const [scope, setScope] = useState<ToastScope>(() => readToastScope());
	const [railCollapsed, setRailCollapsed] = useState<boolean>(() => readRailCollapsed());

	// React to scope + rail-collapse changes (same-tab CustomEvent + cross-tab StorageEvent)
	useEffect(() => {
		function onScope() {
			setScope(readToastScope());
		}
		function onCollapse() {
			setRailCollapsed(readRailCollapsed());
		}
		function onStorage(e: StorageEvent) {
			if (e.key === TOAST_SCOPE_STORAGE_KEY) setScope(readToastScope());
			if (e.key === RAIL_COLLAPSED_STORAGE_KEY) setRailCollapsed(readRailCollapsed());
		}
		window.addEventListener(TOAST_SCOPE_CHANGE_EVENT, onScope);
		window.addEventListener(RAIL_COLLAPSE_CHANGE_EVENT, onCollapse);
		window.addEventListener('storage', onStorage);
		return () => {
			window.removeEventListener(TOAST_SCOPE_CHANGE_EVENT, onScope);
			window.removeEventListener(RAIL_COLLAPSE_CHANGE_EVENT, onCollapse);
			window.removeEventListener('storage', onStorage);
		};
	}, []);

	// Push the latest stream event into the toast queue if it matches scope
	useEffect(() => {
		if (!latest) return;
		if (!matchesToastScope(latest.severity, scope)) return;
		setToasts((prev) => {
			if (prev.some((t) => t.id === latest.id)) return prev;
			return [{ ...latest, addedAt: Date.now() }, ...prev].slice(0, MAX_TOASTS);
		});
	}, [latest, scope]);

	// Auto-dismiss after TOAST_TTL_MS
	useEffect(() => {
		if (toasts.length === 0) return undefined;
		const t = window.setInterval(() => {
			const now = Date.now();
			setToasts((prev) => prev.filter((toast) => now - toast.addedAt < TOAST_TTL_MS));
		}, 250);
		return () => window.clearInterval(t);
	}, [toasts.length]);

	function dismiss(id: string) {
		setToasts((prev) => prev.filter((t) => t.id !== id));
	}

	function handleAction(toast: Toast, action: InlineActionSpec) {
		// Decisions (deny) and the per-item "View" dialog don't belong in a 6s
		// toast — route the operator into the record to decide deliberately.
		if (action.decides || action.opensRequest) {
			const target = primaryDestinationFor(toast);
			if (target) navigate(target);
			dismiss(toast.id);
			return;
		}
		if (action.href && !action.acknowledges) {
			const target = action.href(toast);
			if (target) navigate(target);
			dismiss(toast.id);
			return;
		}
		if (action.acknowledges) {
			void acknowledge(toast.id);
			dismiss(toast.id);
		}
	}

	// Below xl the rail is hidden → toasts at right-4. At xl+ the rail occupies
	// the right side (288px open, 40px collapsed) → shift toasts left of it.
	const xlOffset = railCollapsed ? 'xl:right-14' : 'xl:right-[19rem]';

	if (toasts.length === 0) return null;

	return (
		<div
			className={cn(
				'pointer-events-none fixed right-4 bottom-4 z-[60] flex w-80 flex-col-reverse gap-2',
				xlOffset,
			)}
			role="region"
			aria-label="Agent notifications"
		>
			{toasts.map((toast) => (
				<ToastCard
					key={toast.id}
					toast={toast}
					onDismiss={() => dismiss(toast.id)}
					onAction={(action) => handleAction(toast, action)}
					onOpen={() => {
						const target = primaryDestinationFor(toast);
						if (target) {
							navigate(target);
							dismiss(toast.id);
						}
					}}
				/>
			))}
		</div>
	);
}

function ToastCard({
	toast,
	onDismiss,
	onAction,
	onOpen,
}: {
	toast: Toast;
	onDismiss: () => void;
	onAction: (action: InlineActionSpec) => void;
	onOpen?: () => void;
}) {
	const [progress, setProgress] = useState(100);

	// Kick off the shrink transition on the next frame so the initial 100% paints first.
	useEffect(() => {
		const id = window.requestAnimationFrame(() => setProgress(0));
		return () => window.cancelAnimationFrame(id);
	}, []);

	const headline = KIND_LABEL[toast.kind];
	// In the toast, collapse the access-request actions (View / Deny) into a
	// single "Review" affordance — the actual decision is made in the rail row's
	// dialog (a 6s toast is the wrong place to type a denial reason).
	const rawActions = inlineActionsFor(toast);
	const hasDecision = rawActions.some((a) => a.decides || a.opensRequest);
	const actions: InlineActionSpec[] = hasDecision
		? [{ kind: 'view_request', label: 'Review', opensRequest: true }]
		: rawActions;
	const critical = toast.severity === 'critical' || toast.severity === 'error';

	return (
		<div
			role={critical ? 'alert' : 'status'}
			aria-live={critical ? 'assertive' : 'polite'}
			tabIndex={onOpen ? 0 : undefined}
			onClick={(e) => {
				if (!onOpen) return;
				if ((e.target as HTMLElement).closest('button')) return;
				onOpen();
			}}
			onKeyDown={(e) => {
				if (!onOpen) return;
				if (e.key === 'Enter' || e.key === ' ') {
					e.preventDefault();
					onOpen();
				}
			}}
			className={cn(
				'bg-muted border-border pointer-events-auto rounded-lg border border-l-4 p-3 shadow-2xl',
				severityStripeClass(toast.severity),
				onOpen && 'hover:bg-muted/80 cursor-pointer',
			)}
		>
			<div className="flex items-start gap-2.5">
				<StreamEventIcon ev={toast} className="mt-0.5 h-4 w-4" />
				<div className="min-w-0 flex-1">
					<div className="flex items-center gap-2">
						<span className="text-foreground truncate text-sm font-semibold">
							{headline}
						</span>
						<span className="text-muted-foreground ml-auto shrink-0 font-mono text-[10px]">
							{formatStreamTime(toast.tsMs)}
						</span>
					</div>
					<div className="mt-0.5 flex items-center gap-1.5">
						<span className="text-muted-foreground truncate text-xs">
							{toast.title}
						</span>
					</div>
					{toast.meta && (
						<div className="text-muted-foreground mt-0.5 truncate font-mono text-[10px]">
							{toast.meta}
						</div>
					)}
					{actions.length > 0 && (
						<div className="mt-2 flex flex-wrap gap-1.5">
							{actions.map((action) => (
								<Button
									key={action.kind}
									variant={action.kind === 'acknowledge' ? 'primary' : 'ghost'}
									size="sm"
									onClick={() => onAction(action)}
									className="h-7 px-2.5 text-[11px]"
								>
									{action.label}
								</Button>
							))}
						</div>
					)}
				</div>
				<Button variant="ghost" size="icon" onClick={onDismiss} aria-label="Dismiss toast">
					<X className="h-4 w-4" />
				</Button>
			</div>
			<div className="bg-border mt-2 h-0.5 w-full overflow-hidden rounded-full">
				<div
					className={cn(
						'h-full',
						critical && 'bg-danger',
						toast.severity === 'warning' && 'bg-warning',
						toast.severity === 'info' && 'bg-primary',
					)}
					style={{ width: `${progress}%`, transition: `width ${TOAST_TTL_MS}ms linear` }}
				/>
			</div>
		</div>
	);
}
