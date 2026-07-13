/**
 * WorkspaceStatsStrip — compact dashboard ribbon below the page header.
 *
 * Faithful to jentic-mini's `WorkspaceStatsStrip` visual (a low-noise,
 * labelled, never-clickable strip) but scoped to what the Workspace module
 * owns here: APIs are the only domain on this surface, so the numbers are
 * derived from the already-loaded API list rather than fanning out to the
 * credentials / toolkits / traces endpoints other modules own. That keeps the
 * module boundary clean (no cross-module service calls) while preserving the
 * at-a-glance ribbon mini users expect.
 */
import { Boxes, GitBranch, ShieldCheck, Zap, FileClock } from 'lucide-react';
import { cn } from '@/shared/lib/utils';
import type { WorkspaceApi } from '@/modules/workspace/api';

export interface WorkspaceStatsStripProps {
	apis: WorkspaceApi[];
	loading: boolean;
}

export function WorkspaceStatsStrip({ apis, loading }: WorkspaceStatsStripProps) {
	const apiCount = apis.length;
	const operationCount = apis.reduce((sum, a) => sum + (a.operationCount ?? 0), 0);
	const revisionCount = apis.reduce((sum, a) => sum + (a.revisionCount ?? 0), 0);
	const draftOnlyCount = apis.filter((a) => a.currentRevisionId === null).length;
	const securityCount = new Set(apis.flatMap((a) => a.securitySchemes)).size;

	return (
		<div
			className="border-border/60 bg-muted/20 text-muted-foreground -mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border px-4 py-3 text-xs"
			data-testid="workspace-stats-strip"
		>
			<Stat
				icon={<Boxes size={13} aria-hidden="true" />}
				label="APIs"
				value={apiCount}
				loading={loading}
				testId="workspace-stat-apis"
			/>
			<Stat
				icon={<Zap size={13} aria-hidden="true" />}
				label="Operations"
				value={operationCount}
				loading={loading}
				testId="workspace-stat-operations"
			/>
			<Stat
				icon={<GitBranch size={13} aria-hidden="true" />}
				label="Revisions"
				value={revisionCount}
				loading={loading}
				testId="workspace-stat-revisions"
			/>
			<Stat
				icon={<ShieldCheck size={13} aria-hidden="true" />}
				label="Security schemes"
				value={securityCount}
				loading={loading}
				testId="workspace-stat-security"
			/>
			<Stat
				icon={<FileClock size={13} aria-hidden="true" />}
				label="Drafts"
				value={draftOnlyCount}
				loading={loading}
				testId="workspace-stat-drafts"
				className="ml-auto"
			/>
		</div>
	);
}

function Stat({
	icon,
	label,
	value,
	loading,
	testId,
	className,
}: {
	icon: React.ReactNode;
	label: string;
	value: number | string | null;
	loading: boolean;
	testId: string;
	className?: string;
}) {
	const display = loading
		? '…'
		: value === null || value === undefined
			? '—'
			: typeof value === 'number'
				? value.toLocaleString()
				: value;
	return (
		<span className={cn('inline-flex items-baseline gap-2', className)} data-testid={testId}>
			<span className="text-muted-foreground/70 inline-flex items-center gap-1.5 self-center">
				{icon}
				<span className="text-[10px] tracking-wider uppercase">{label}</span>
			</span>
			<span className="text-foreground font-mono text-sm font-medium">{display}</span>
		</span>
	);
}
