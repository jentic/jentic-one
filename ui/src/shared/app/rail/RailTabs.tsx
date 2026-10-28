/**
 * RailTabs — the rail's two tabs, **Inbox** | Activity: the held calls waiting
 * on the viewer, and the live feed. Compact pills sized for the 288px rail
 * (`TabNav` is page navigation, with an underline and a full-width hairline).
 *
 *   • Inbox carries the shield glyph (amber while anything waits) and the
 *     waiting count.
 *   • Activity carries the stream's status light, so the tab doubles as the
 *     feed's "live" indicator.
 *
 * The WAI-ARIA tabs pattern with automatic activation: roving tabIndex, and
 * Left/Right/Home/End move focus and selection together. Stateless: the rail
 * body owns the selection.
 */
import { useRef, type KeyboardEvent, type ReactNode } from 'react';
import { ShieldQuestion } from 'lucide-react';
import { ATTENTION_CHIP_CLASS } from '@/shared/approvals/attentionTone';
import type { RailTab } from '@/shared/app/rail/railPreferences';
import { cn } from '@/shared/lib/utils';

const TABS: RailTab[] = ['inbox', 'activity'];

/** The DOM id of a tab and of the panel it controls, under one rail body. */
export function railTabId(baseId: string, tab: RailTab): string {
	return `${baseId}-tab-${tab}`;
}

export function railPanelId(baseId: string, tab: RailTab): string {
	return `${baseId}-panel-${tab}`;
}

export type RailTabsProps = {
	/** Prefix for the tab and panel ids (`useId`), unique per rail body. */
	baseId: string;
	value: RailTab;
	onChange: (tab: RailTab) => void;
	/** Held calls waiting on the viewer; `atLeast` when more exist than were read. */
	inboxCount: number;
	inboxAtLeast?: boolean;
	/** The stream's status light (`LiveDot`), shown inside the Activity tab. */
	activityDot: ReactNode;
};

export function RailTabs({
	baseId,
	value,
	onChange,
	inboxCount,
	inboxAtLeast = false,
	activityDot,
}: RailTabsProps) {
	const refs = useRef(new Map<RailTab, HTMLButtonElement>());

	function handleKeyDown(e: KeyboardEvent<HTMLButtonElement>) {
		const idx = TABS.indexOf(value);
		let next: RailTab | null = null;
		if (e.key === 'ArrowRight') next = TABS[(idx + 1) % TABS.length];
		else if (e.key === 'ArrowLeft') next = TABS[(idx - 1 + TABS.length) % TABS.length];
		else if (e.key === 'Home') next = TABS[0];
		else if (e.key === 'End') next = TABS[TABS.length - 1];
		if (!next) return;
		e.preventDefault();
		onChange(next);
		refs.current.get(next)?.focus();
	}

	const countLabel = inboxAtLeast ? `${inboxCount}+` : String(inboxCount);

	return (
		<div role="tablist" aria-label="Rail" className="flex min-w-0 items-center gap-1">
			{TABS.map((tab) => {
				const active = tab === value;
				return (
					<button
						key={tab}
						ref={(el) => {
							if (el) refs.current.set(tab, el);
							else refs.current.delete(tab);
						}}
						type="button"
						role="tab"
						id={railTabId(baseId, tab)}
						aria-selected={active}
						aria-controls={railPanelId(baseId, tab)}
						tabIndex={active ? 0 : -1}
						data-rail-tab={tab}
						onClick={() => onChange(tab)}
						onKeyDown={handleKeyDown}
						className={cn(
							'focus-visible:ring-ring inline-flex h-7 shrink-0 cursor-pointer items-center gap-1.5 rounded-md px-2 text-[13px] transition-colors focus-visible:ring-2 focus-visible:outline-none',
							active
								? 'bg-surface-tonal text-foreground-name font-semibold'
								: 'text-muted-foreground hover:text-foreground hover:bg-tint-2 font-medium',
						)}
					>
						{tab === 'inbox' ? (
							<>
								<ShieldQuestion
									aria-hidden="true"
									className={cn(
										'h-3.5 w-3.5',
										inboxCount > 0 ? 'text-warning' : 'text-muted-foreground',
									)}
								/>
								Inbox
								{inboxCount > 0 && (
									<span
										className={cn(
											'grid h-4 min-w-4 place-items-center rounded-full px-1 text-[10px] font-bold tabular-nums',
											ATTENTION_CHIP_CLASS,
										)}
									>
										{countLabel}
										<span className="sr-only"> waiting</span>
									</span>
								)}
							</>
						) : (
							<>
								{activityDot}
								Activity
							</>
						)}
					</button>
				);
			})}
		</div>
	);
}
