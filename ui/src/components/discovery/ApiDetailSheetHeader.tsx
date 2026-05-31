import { ArrowLeft, Globe, Workflow, X, Zap } from 'lucide-react';
import { useRecentInspects } from './recentInspectStore';
import { VendorIcon } from './VendorIcon';
import type { DiscoverySource } from './DiscoveryCard';
import { Button } from '@/components/ui/Button';
import { CopyButton } from '@/components/ui/CopyButton';

export function SheetHeader({
	title,
	apiId,
	source,
	hasWorkflows,
	onClose,
	onBack,
	onSelectApi,
}: {
	title: string;
	apiId: string;
	source: DiscoverySource;
	hasWorkflows?: boolean;
	onClose: () => void;
	onBack?: () => void;
	onSelectApi?: (apiId: string) => void;
}) {
	const { entries } = useRecentInspects();
	const showRecents = !onBack && onSelectApi && entries.length > 1;

	return (
		<div className="border-border/60 bg-card sticky top-0 z-10 border-b">
			<div className="flex items-start gap-3 p-5">
				{onBack ? (
					<Button
						variant="ghost"
						size="icon"
						onClick={onBack}
						className="shrink-0"
						aria-label="Back to operations"
					>
						<ArrowLeft className="h-4 w-4" />
					</Button>
				) : (
					<VendorIcon name={title} vendor={apiId} />
				)}
				<div className="min-w-0 flex-1">
					<h2
						id="api-detail-title"
						className="text-foreground truncate text-base leading-tight font-semibold"
					>
						{title}
					</h2>
					{title !== apiId && (
						<div className="mt-0.5 flex items-center gap-1.5">
							<code className="text-muted-foreground truncate font-mono text-xs">
								{apiId}
							</code>
							<CopyButton value={apiId} />
						</div>
					)}
					<div className="mt-2 flex flex-wrap items-center gap-1.5">
						<SourcePill source={source} />
						{hasWorkflows && (
							<span className="inline-flex items-center gap-1 rounded-full bg-teal-500/10 px-2 py-0.5 text-xs font-medium text-teal-400 ring-1 ring-teal-500/20">
								<Workflow size={11} /> workflows
							</span>
						)}
					</div>
				</div>
				<Button
					variant="ghost"
					size="icon"
					onClick={onClose}
					className="shrink-0"
					aria-label="Close detail panel"
				>
					<X className="h-4 w-4" />
				</Button>
			</div>
			{showRecents && <RecentInspectsStrip apiId={apiId} onSelectApi={onSelectApi} />}
		</div>
	);
}

/**
 * Compact "recently inspected" chip strip below the sheet header.
 * Renders the last few APIs the user opened in the sheet, current one
 * highlighted with `aria-current`. Click swaps the sheet target without
 * closing it.
 */
function RecentInspectsStrip({
	apiId,
	onSelectApi,
}: {
	apiId: string;
	onSelectApi: (apiId: string) => void;
}) {
	const { entries } = useRecentInspects();

	return (
		<div
			className="border-border/40 bg-muted/20 flex flex-wrap items-center gap-1.5 border-t px-5 py-2"
			data-testid="sheet-recents-strip"
			aria-label="Recently inspected APIs"
		>
			<span className="text-muted-foreground/80 mr-1 text-[10px] font-medium tracking-wider uppercase">
				Recents
			</span>
			{entries.slice(0, 4).map((entry) => {
				const isCurrent = entry.apiId === apiId;
				return (
					<button
						type="button"
						key={entry.apiId}
						onClick={() => {
							if (!isCurrent) onSelectApi(entry.apiId);
						}}
						aria-current={isCurrent ? 'true' : undefined}
						disabled={isCurrent}
						data-testid="sheet-recents-chip"
						className={
							'group focus-visible:ring-ring inline-flex max-w-[160px] items-center gap-1.5 rounded-full py-1 pr-2.5 pl-1 text-xs transition-colors focus-visible:ring-2 focus-visible:ring-offset-1 ' +
							(isCurrent
								? 'bg-primary/15 text-foreground ring-primary/30 cursor-default ring-1'
								: 'bg-card text-muted-foreground hover:bg-muted hover:text-foreground border-border/60 border')
						}
					>
						{/*
						 * Force the icon's corners to match the pill's `rounded-full`
						 * so the leading logo doesn't read as a square sticker glued
						 * onto a circle. `!rounded-full` overrides the default
						 * `rounded-[8px]` baked into VendorIcon's `sm` size token.
						 */}
						<VendorIcon
							name={entry.name ?? entry.apiId}
							vendor={entry.apiId}
							size="sm"
							className="!rounded-full"
						/>
						<span className="truncate">{entry.name ?? entry.apiId}</span>
					</button>
				);
			})}
		</div>
	);
}

export function SourcePill({ source }: { source: DiscoverySource }) {
	const isWorkspace = source === 'workspace';
	const cls = isWorkspace
		? 'bg-emerald-500/10 text-emerald-400 ring-emerald-500/20'
		: 'border-border/70 bg-transparent text-muted-foreground ring-border/60';
	return (
		<span
			className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${cls}`}
		>
			{isWorkspace ? <Zap size={11} /> : <Globe size={11} />}
			{isWorkspace ? 'Imported' : 'Available'}
		</span>
	);
}
