/**
 * Shared scaffolding for the agent / service-account detail consoles — the
 * pieces both pages (and their tab panels) would otherwise copy. The card
 * shells themselves (`DetailSection`, `DangerZone`, `IdentitySettingsCard`,
 * `AuditTrailCard`) are shared product-wide from `@/shared/ui`.
 */
import type { ReactNode } from 'react';

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
