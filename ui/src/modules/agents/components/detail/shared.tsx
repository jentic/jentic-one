/**
 * Shared scaffolding for the agent / service-account detail consoles — the
 * pieces both pages (and their tab panels) would otherwise copy. Mirrors the
 * toolkit console's `components/detail/shared.tsx` precedent so the two
 * console families stay structurally alike.
 */
import type { ReactNode } from 'react';
import { TriangleAlert } from 'lucide-react';
import { Button, Card, CardBody, CardHeader, CardTitle } from '@/shared/ui';

/** A compact label/value pair used in the attribution / key meta grids. */
export function MetaItem({ label, value }: { label: string; value: ReactNode }) {
	return (
		<div className="min-w-0">
			<dt className="text-muted-foreground/70 text-[10px] tracking-wider uppercase">
				{label}
			</dt>
			<dd className="text-foreground/90 mt-0.5 truncate text-xs">{value}</dd>
		</div>
	);
}

/** "99.2%" success share, or an em-dash when there's no traffic to judge. */
export function successShare(success: number, total: number): string {
	if (total === 0) return '—';
	return `${((success / total) * 100).toFixed(1).replace(/\.0$/, '')}%`;
}

export interface DangerZoneItem {
	action: 'disable' | 'archive';
	title: string;
	description: string;
	/** Accessible label carrying the actor name (e.g. "Disable support-agent"). */
	ariaLabel: string;
}

interface DangerZoneProps {
	items: DangerZoneItem[];
	/** True while any page-level lifecycle mutation is in flight. */
	pending: boolean;
	/** Ask the page to run the action (opens its confirm dialog). */
	onAction: (action: 'disable' | 'archive') => void;
}

/** Button label for each danger-zone verb (matches the kebab vocabulary). */
const DANGER_LABEL: Record<DangerZoneItem['action'], string> = {
	disable: 'Disable',
	archive: 'Archive',
};

/**
 * The Settings tab's destructive-actions card, shared by both consoles.
 * Renders nothing when no destructive verb applies to the actor's status.
 * Disable (reversible) reads as an outline; Archive (terminal) as solid
 * danger. Actions always defer to the page-level confirm dialogs — this
 * component never mutates lifecycle state itself.
 */
export function DangerZone({ items, pending, onAction }: DangerZoneProps) {
	if (items.length === 0) return null;
	return (
		<Card className="border-danger/30">
			<CardHeader>
				<CardTitle className="text-danger flex items-center gap-2">
					<TriangleAlert className="h-4 w-4" aria-hidden />
					Danger zone
				</CardTitle>
			</CardHeader>
			<CardBody className="divide-border/60 divide-y">
				{items.map((item) => (
					<div
						key={item.action}
						className="flex flex-wrap items-center justify-between gap-3 py-3 first:pt-0 last:pb-0"
					>
						<div className="min-w-0">
							<p className="text-foreground text-sm font-medium">{item.title}</p>
							<p className="text-muted-foreground text-xs">{item.description}</p>
						</div>
						<Button
							size="sm"
							variant={item.action === 'archive' ? 'danger' : 'outline'}
							className={
								item.action === 'archive'
									? 'shrink-0'
									: 'border-danger/40 text-danger hover:bg-danger/10 shrink-0'
							}
							disabled={pending}
							onClick={() => onAction(item.action)}
							aria-label={item.ariaLabel}
						>
							{DANGER_LABEL[item.action]}
						</Button>
					</div>
				))}
			</CardBody>
		</Card>
	);
}
