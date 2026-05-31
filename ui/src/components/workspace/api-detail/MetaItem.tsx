import type { ReactNode } from 'react';

interface MetaItemProps {
	icon: ReactNode;
	label: string;
	value: string;
	loading?: boolean;
}

/**
 * Single icon + uppercase label + monospace value pair used inside the
 * overview strip. Kept tiny and presentation-only so it can be reused
 * elsewhere if the detail surface grows additional summary rows.
 */
export function MetaItem({ icon, label, value, loading }: MetaItemProps) {
	return (
		<span className="inline-flex min-w-0 items-baseline gap-2">
			<span className="text-muted-foreground/70 inline-flex shrink-0 items-center gap-1.5 self-center">
				{icon}
				<span className="text-[10px] tracking-wider uppercase">{label}</span>
			</span>
			<span className="text-foreground font-mono text-sm font-medium">
				{loading ? '…' : value}
			</span>
		</span>
	);
}
