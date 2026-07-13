/**
 * RailHeader — the control surface for the live feed.
 *
 *   • a live-status dot + label reflecting the SSE connection
 *     (connecting / live / error)
 *   • a visible PAUSED pill only when the feed is MANUALLY paused; a quiet
 *     "Holding" hint while the cursor is in the rail (new events held, not a
 *     pause — the live dot keeps pulsing and the button stays "Pause")
 *   • manual pause toggle
 *   • Cmd/Ctrl-F focused filter
 *   • filter chips for severity and kind
 *   • stale-feed warning when the SSE has errored or is reconnecting
 *
 * Stateless: parent owns the state, this just emits change events.
 */
import { useEffect, useRef, useState } from 'react';
import {
	ChevronRight,
	Download,
	Filter,
	History,
	MoreVertical,
	Pause,
	Play,
	Volume2,
} from 'lucide-react';
import { Button } from '@/shared/ui/Button';
import { SearchInput } from '@/shared/ui/SearchInput';
import type { StreamEvent, StreamStatus } from '@/shared/lib/agentStream';
import { cn } from '@/shared/lib/utils';

// Kind + severity chips cover the full taxonomy the rail can render, including
// types the backend declares but does not yet emit (`credential.*`,
// `execution.repeated_failure`) — kept for forward-compat so wiring them later
// is zero-effort. Reachable today: import/execution/access_request kinds and
// info/error severities. Backend emitters tracked in jentic/jentic-one#397
// (+ #387 actor filter, #389 _links.action).
const KIND_LABEL: Record<StreamEvent['kind'], string> = {
	import: 'imports',
	execution: 'executions',
	access_request: 'access',
	credential: 'creds',
	other: 'other',
};
const ALL_KINDS = Object.keys(KIND_LABEL) as Array<StreamEvent['kind']>;

const SEVERITY_LABEL: Record<StreamEvent['severity'], string> = {
	critical: 'critical',
	error: 'error',
	warning: 'warning',
	info: 'info',
};
const ALL_SEVERITIES = Object.keys(SEVERITY_LABEL) as Array<StreamEvent['severity']>;

const SEVERITY_DOT: Record<StreamEvent['severity'], string> = {
	critical: 'bg-danger',
	error: 'bg-danger',
	warning: 'bg-warning',
	info: 'bg-primary',
};

export type RailHeaderProps = {
	eventCount: number;
	status: StreamStatus;
	/** Manual (explicit) pause — drives the Play/Pause button + amber dot. */
	paused: boolean;
	/**
	 * Cursor is inside the rail and we're quietly holding new events so the list
	 * doesn't shift under a click. NOT a "pause": the button stays "Pause" and
	 * the live dot keeps pulsing. Only shown when not manually paused.
	 */
	hoverFrozen: boolean;
	/** How many new events are being held back while the feed is frozen. */
	heldBack: number;
	stale: boolean; // SSE errored or reconnecting after a drop
	audioOnCritical: boolean;
	onTogglePause: () => void;
	onCollapse: () => void;
	onLoadOlder: () => void;
	onExportTraceBundle: () => void;
	loadingOlder?: boolean;
	canLoadOlder?: boolean;
	search: string;
	onSearchChange: (v: string) => void;
	severities: Set<StreamEvent['severity']>;
	onToggleSeverity: (s: StreamEvent['severity']) => void;
	kinds: Set<StreamEvent['kind']>;
	onToggleKind: (k: StreamEvent['kind']) => void;
	onClearFilters: () => void;
};

export function RailHeader({
	eventCount,
	status,
	paused,
	hoverFrozen,
	heldBack,
	stale,
	audioOnCritical,
	onTogglePause,
	onCollapse,
	onLoadOlder,
	onExportTraceBundle,
	loadingOlder,
	canLoadOlder = true,
	search,
	onSearchChange,
	severities,
	onToggleSeverity,
	kinds,
	onToggleKind,
	onClearFilters,
}: RailHeaderProps) {
	const searchRef = useRef<HTMLInputElement | null>(null);
	const [menuOpen, setMenuOpen] = useState(false);
	const menuRef = useRef<HTMLDivElement | null>(null);
	const menuTriggerRef = useRef<HTMLButtonElement | null>(null);

	useEffect(() => {
		if (!menuOpen) return;
		function onClick(e: MouseEvent) {
			const target = e.target as Node | null;
			if (target && menuRef.current && !menuRef.current.contains(target)) {
				setMenuOpen(false);
			}
		}
		function onKeyDown(e: KeyboardEvent) {
			if (e.key === 'Escape') {
				setMenuOpen(false);
				menuTriggerRef.current?.focus();
			}
		}
		window.addEventListener('mousedown', onClick);
		window.addEventListener('keydown', onKeyDown);
		return () => {
			window.removeEventListener('mousedown', onClick);
			window.removeEventListener('keydown', onKeyDown);
		};
	}, [menuOpen]);

	// Cmd/Ctrl-F focuses the rail filter. The rail is a passive sidebar that
	// rarely holds focus, so we intercept on the CAPTURE phase from anywhere on
	// the page (a focus-scoped guard would mean the browser's find-in-page always
	// won). To avoid being a keyboard trap we DON'T hijack when:
	//   • the rail is collapsed/hidden (offsetParent === null) — let native find
	//     work; or
	//   • the user is typing in another text field (input/textarea/select/
	//     contenteditable) outside the rail — they may want native find there.
	// Esc clears + blurs the filter when it's focused.
	useEffect(() => {
		function isEditableTarget(el: EventTarget | null): boolean {
			if (!(el instanceof HTMLElement)) return false;
			if (el === searchRef.current) return false; // our own field is fine
			const tag = el.tagName;
			return (
				tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable
			);
		}
		function onKey(e: KeyboardEvent) {
			if (!((e.metaKey || e.ctrlKey) && (e.key === 'f' || e.key === 'F'))) {
				if (e.key === 'Escape' && document.activeElement === searchRef.current) {
					onSearchChange('');
					searchRef.current?.blur();
				}
				return;
			}
			const input = searchRef.current;
			const railEl = input?.closest('aside');
			// offsetParent is null when the element (or an ancestor) is display:none
			// — i.e. the rail is collapsed/hidden, so don't steal the browser's find.
			if (!input || !railEl || railEl.offsetParent === null) return;
			// Don't steal find while the user is typing in another field.
			if (isEditableTarget(e.target)) return;
			e.preventDefault();
			input.focus();
			input.select();
		}
		window.addEventListener('keydown', onKey, true);
		return () => window.removeEventListener('keydown', onKey, true);
	}, [onSearchChange]);

	const filtersActive = search.length > 0 || severities.size > 0 || kinds.size > 0;

	return (
		<div className="border-border bg-background/60 border-b">
			<div className="flex items-center gap-2 px-3 py-2">
				<div className="flex min-w-0 flex-1 items-center gap-2">
					<span
						role="img"
						aria-label={
							paused
								? 'Feed paused'
								: status === 'live'
									? 'Stream live'
									: status === 'error'
										? 'Stream offline'
										: 'Connecting to stream'
						}
						className={cn(
							'h-2 w-2 shrink-0 rounded-full',
							paused
								? 'bg-warning'
								: status === 'live'
									? 'bg-success animate-pulse'
									: status === 'error'
										? 'bg-danger'
										: 'bg-muted-foreground animate-pulse',
						)}
					/>
					<span className="text-foreground shrink-0 text-sm font-semibold">
						Agent rail
					</span>
					{status === 'connecting' && (
						<span
							className="text-muted-foreground shrink-0 font-mono text-[9px] tracking-widest uppercase"
							title="Connecting to the live event stream…"
						>
							Connecting
						</span>
					)}
					{status === 'error' && (
						<span
							className="border-danger/40 bg-danger/10 text-danger shrink-0 rounded border px-1 py-0 font-mono text-[9px] font-bold tracking-widest uppercase"
							title="The live event stream is disconnected — showing the backlog."
						>
							Offline
						</span>
					)}
					{audioOnCritical && (
						<span
							className="text-primary shrink-0"
							role="img"
							title="Audio cue on critical events is on"
							aria-label="Audio on critical enabled"
						>
							<Volume2 className="h-3.5 w-3.5" />
						</span>
					)}
					{paused && (
						<span
							className="text-warning min-w-0 truncate font-mono text-[10px] tracking-widest uppercase"
							title="Feed paused — click Play to resume"
						>
							PAUSED
						</span>
					)}
					{!paused && hoverFrozen && (
						<span
							className="text-muted-foreground min-w-0 shrink-0 truncate font-mono text-[10px] tracking-widest uppercase"
							title="New events held while your cursor is in the rail — move away to resume"
						>
							Holding{heldBack > 0 ? ` · ${heldBack}` : ''}
						</span>
					)}
				</div>
				<div className="flex shrink-0 items-center gap-1">
					<Button
						variant="ghost"
						size="icon"
						onClick={onTogglePause}
						aria-label={paused ? 'Resume live feed' : 'Pause live feed'}
						title={paused ? 'Resume' : 'Pause'}
					>
						{paused ? (
							<Play className="text-muted-foreground h-3.5 w-3.5" />
						) : (
							<Pause className="text-muted-foreground h-3.5 w-3.5" />
						)}
					</Button>
					<div ref={menuRef} className="relative">
						<Button
							ref={menuTriggerRef}
							variant="ghost"
							size="icon"
							onClick={() => setMenuOpen((o) => !o)}
							aria-haspopup="menu"
							aria-expanded={menuOpen}
							aria-label="Rail menu"
							title="More"
						>
							<MoreVertical className="text-muted-foreground h-3.5 w-3.5" />
						</Button>
						{menuOpen && (
							<div
								role="menu"
								className="border-border bg-background absolute right-0 z-20 mt-1 w-56 overflow-hidden rounded-md border shadow-lg"
							>
								<button
									type="button"
									role="menuitem"
									onClick={() => {
										setMenuOpen(false);
										onLoadOlder();
									}}
									disabled={loadingOlder || !canLoadOlder}
									className="text-foreground hover:bg-muted flex w-full items-center gap-2 px-3 py-2 text-left text-xs disabled:opacity-50"
								>
									<History className="h-3.5 w-3.5" />
									{loadingOlder
										? 'Loading older…'
										: canLoadOlder
											? 'Load older →'
											: 'No older events'}
								</button>
								<button
									type="button"
									role="menuitem"
									onClick={() => {
										setMenuOpen(false);
										onExportTraceBundle();
									}}
									className="text-foreground hover:bg-muted flex w-full items-center gap-2 px-3 py-2 text-left text-xs"
								>
									<Download className="h-3.5 w-3.5" />
									Export recent events as trace bundle
								</button>
							</div>
						)}
					</div>
					<Button
						variant="ghost"
						size="icon"
						onClick={onCollapse}
						aria-label="Collapse agent rail"
						title="Collapse"
					>
						<ChevronRight className="text-muted-foreground h-4 w-4" />
					</Button>
				</div>
			</div>

			<div className="px-3 pb-2">
				<SearchInput
					ref={searchRef}
					size="sm"
					value={search}
					onValueChange={onSearchChange}
					icon={<Filter className="h-3.5 w-3.5" />}
					placeholder="Filter events · ⌘F"
					aria-label="Filter rail events"
				/>
			</div>

			<div className="flex flex-wrap items-center gap-1 px-3 pb-2">
				{ALL_SEVERITIES.map((s) => {
					const active = severities.has(s);
					return (
						<button
							key={s}
							type="button"
							onClick={() => onToggleSeverity(s)}
							aria-pressed={active}
							className={cn(
								'flex items-center gap-1 rounded-full border px-1.5 py-0.5 font-mono text-[10px] tracking-wider uppercase',
								active
									? 'border-primary bg-primary/15 text-primary'
									: 'border-border text-muted-foreground hover:text-foreground',
							)}
						>
							<span className={cn('h-1.5 w-1.5 rounded-full', SEVERITY_DOT[s])} />
							{SEVERITY_LABEL[s]}
						</button>
					);
				})}
				{ALL_KINDS.map((k) => {
					const active = kinds.has(k);
					return (
						<button
							key={k}
							type="button"
							onClick={() => onToggleKind(k)}
							aria-pressed={active}
							className={cn(
								'rounded-full border px-1.5 py-0.5 font-mono text-[10px] tracking-wider uppercase',
								active
									? 'border-primary bg-primary/15 text-primary'
									: 'border-border text-muted-foreground hover:text-foreground',
							)}
						>
							{KIND_LABEL[k]}
						</button>
					);
				})}
				{filtersActive && (
					<button
						type="button"
						onClick={onClearFilters}
						className="text-muted-foreground hover:text-foreground ml-auto font-mono text-[10px] tracking-wider uppercase"
					>
						clear
					</button>
				)}
			</div>

			<div className="text-muted-foreground flex items-center justify-between px-3 pb-2 font-mono text-[10px] tracking-widest uppercase">
				<span>{eventCount} events</span>
				{stale && (
					<span className="text-warning normal-case">Feed delayed — reconnecting…</span>
				)}
			</div>
		</div>
	);
}
