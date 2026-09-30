/**
 * NotificationsMenu — the one place the app tells you something needs you.
 *
 * A top-bar bell whose badge is the shared `useAttentionItems` count (the
 * same number Home leads with), opening a panel with the grouped list and the
 * verbs to clear it inline. The rule it enforces: if a human has to act, it's
 * here; if it's just information, it's in Activity (the rail and Monitor).
 *
 * The panel also owns the notification preferences — which events pop a
 * toast, and the sound on failures — so they live next to the notifications
 * they govern instead of in the activity feed's overflow menu.
 */
import { useCallback, useId, useState } from 'react';
import { ArrowLeft, Bell, CheckCircle2, Inbox, Settings2 } from 'lucide-react';
import { AppLink } from '@/shared/ui/AppLink';
import { Button } from '@/shared/ui/Button';
import { useDismissable, useViewportClamp } from '@/shared/ui/Menu';
import { SkeletonRows } from '@/shared/ui';
import { ROUTES } from '@/shared/app/routes';
import { useRailPreferences } from '@/shared/app/rail/railPreferences';
import { AttentionList } from '@/shared/attention/AttentionList';
import { useAttentionItems } from '@/shared/attention/useAttentionItems';
import type { ToastScope } from '@/shared/lib/agentStream';
import { cn } from '@/shared/lib/utils';

/** `off` behaves like `critical` (failures always toast), so it shows as that. */
const TOAST_SCOPE_OPTIONS: Array<{ value: ToastScope; label: string; hint: string }> = [
	{ value: 'all', label: 'Every event', hint: 'Imports, sign-ins, approvals and failures.' },
	{ value: 'warning', label: 'Warnings and failures', hint: 'Skip routine successes.' },
	{ value: 'critical', label: 'Failures only', hint: 'Quietest. Failures always pop up.' },
];

export function NotificationsMenu() {
	const { items, count, isLoading, failedSources } = useAttentionItems();
	const [open, setOpen] = useState(false);
	const [view, setView] = useState<'list' | 'settings'>('list');
	const close = useCallback(() => {
		setOpen(false);
		setView('list');
	}, []);
	const containerRef = useDismissable<HTMLDivElement>(open, close);
	const panelRef = useViewportClamp<HTMLDivElement>(open);
	const titleId = useId();

	const urgent = items.some((i) => i.urgency === 3);
	const label =
		count > 0 ? `Notifications (${count} need${count === 1 ? 's' : ''} you)` : 'Notifications';

	return (
		<div ref={containerRef} className="relative">
			<button
				type="button"
				onClick={() => (open ? close() : setOpen(true))}
				aria-label={label}
				title={label}
				aria-haspopup="dialog"
				aria-expanded={open}
				className={cn(
					'text-muted-foreground hover:bg-muted hover:text-foreground relative flex h-7 w-7 shrink-0 items-center justify-center rounded-md transition-colors duration-150',
					open && 'bg-muted text-foreground',
				)}
			>
				<Bell className="h-4 w-4" aria-hidden="true" />
				{count > 0 && (
					<span
						className={cn(
							'ring-background absolute -top-0.5 -right-1 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] leading-none font-semibold tabular-nums ring-2',
							urgent ? 'bg-danger text-white' : 'bg-warning text-black',
						)}
						aria-hidden="true"
					>
						{count > 99 ? '99+' : count}
					</span>
				)}
			</button>

			{open && (
				<div
					ref={panelRef}
					role="dialog"
					aria-labelledby={titleId}
					className="border-border bg-background absolute top-full right-0 z-50 mt-2 flex max-h-[min(36rem,calc(100dvh-4.5rem))] w-[min(24rem,calc(100vw-1.5rem))] flex-col overflow-hidden rounded-xl border shadow-xl"
				>
					{view === 'list' ? (
						<>
							<div className="border-border flex items-center justify-between gap-2 border-b px-3 py-2.5">
								<h2
									id={titleId}
									className="text-foreground flex items-center gap-2 text-sm font-semibold"
								>
									Notifications
									{count > 0 && (
										<span
											className={cn(
												'rounded-full px-1.5 py-0.5 font-mono text-[11px] tabular-nums',
												urgent
													? 'bg-danger/15 text-danger'
													: 'bg-warning/15 text-warning',
											)}
										>
											{count}
										</span>
									)}
								</h2>
								<Button
									variant="ghost"
									size="sm"
									className="h-7 w-7 p-0"
									aria-label="Notification settings"
									title="Notification settings"
									onClick={() => setView('settings')}
								>
									<Settings2 className="h-4 w-4" aria-hidden="true" />
								</Button>
							</div>

							<div className="min-h-0 flex-1 overflow-y-auto">
								{isLoading && count === 0 ? (
									<div className="px-3 py-3">
										<SkeletonRows rows={2} />
									</div>
								) : count === 0 ? (
									<div className="flex flex-col items-center gap-2 px-6 py-8 text-center">
										<CheckCircle2
											className="text-accent-green h-6 w-6"
											aria-hidden="true"
										/>
										<p className="text-foreground text-sm font-medium">
											You're all caught up
										</p>
										<p className="text-muted-foreground text-xs">
											Approvals, failures and unfinished sign-ins show up
											here.
										</p>
									</div>
								) : (
									<AttentionList
										items={items}
										variant="menu"
										onNavigate={close}
									/>
								)}
								{failedSources.length > 0 && (
									<p
										role="status"
										className="text-warning border-border flex items-center gap-2 border-t px-3 py-2 text-xs"
									>
										<Inbox
											className="h-3.5 w-3.5 shrink-0"
											aria-hidden="true"
										/>
										Couldn't load {failedSources.join(', ')} — this list may be
										incomplete.
									</p>
								)}
							</div>

							<div className="border-border bg-muted/30 flex items-center justify-end border-t px-3 py-2 text-xs font-medium">
								<AppLink
									href={ROUTES.monitor}
									onClick={close}
									className="text-muted-foreground hover:text-foreground"
								>
									All activity →
								</AppLink>
							</div>
						</>
					) : (
						<NotificationSettings titleId={titleId} onBack={() => setView('list')} />
					)}
				</div>
			)}
		</div>
	);
}

function NotificationSettings({ titleId, onBack }: { titleId: string; onBack: () => void }) {
	const { toastScope, setToastScope, audioOnCritical, setAudioOnCritical } = useRailPreferences();
	const effectiveScope: ToastScope = toastScope === 'off' ? 'critical' : toastScope;
	const groupName = useId();

	return (
		<>
			<div className="border-border flex items-center gap-1.5 border-b px-2 py-2.5">
				<Button
					variant="ghost"
					size="sm"
					className="h-7 w-7 p-0"
					aria-label="Back to notifications"
					onClick={onBack}
				>
					<ArrowLeft className="h-4 w-4" aria-hidden="true" />
				</Button>
				<h2 id={titleId} className="text-foreground text-sm font-semibold">
					Notification settings
				</h2>
			</div>
			<div className="space-y-4 overflow-y-auto px-3 py-3">
				<fieldset>
					<legend className="text-foreground text-xs font-semibold">Pop-up alerts</legend>
					<p className="text-muted-foreground mt-0.5 text-xs">
						Which live events show a toast in the corner.
					</p>
					<div className="mt-2 space-y-1">
						{TOAST_SCOPE_OPTIONS.map((opt) => (
							<label
								key={opt.value}
								className={cn(
									'flex cursor-pointer items-start gap-2.5 rounded-lg border px-2.5 py-2 transition-colors',
									effectiveScope === opt.value
										? 'border-primary/50 bg-primary/5'
										: 'border-border hover:bg-muted/50',
								)}
							>
								<input
									type="radio"
									name={groupName}
									value={opt.value}
									checked={effectiveScope === opt.value}
									onChange={() => setToastScope(opt.value)}
									className="accent-primary mt-0.5"
								/>
								<span>
									<span className="text-foreground block text-[13px] font-medium">
										{opt.label}
									</span>
									<span className="text-muted-foreground block text-xs">
										{opt.hint}
									</span>
								</span>
							</label>
						))}
					</div>
				</fieldset>
				<label className="border-border flex cursor-pointer items-center justify-between gap-3 rounded-lg border px-2.5 py-2">
					<span>
						<span className="text-foreground block text-[13px] font-medium">
							Sound on failures
						</span>
						<span className="text-muted-foreground block text-xs">
							A short chime when something fails.
						</span>
					</span>
					<input
						type="checkbox"
						role="switch"
						checked={audioOnCritical}
						onChange={(e) => setAudioOnCritical(e.target.checked)}
						className="accent-primary h-4 w-4"
					/>
				</label>
			</div>
		</>
	);
}
