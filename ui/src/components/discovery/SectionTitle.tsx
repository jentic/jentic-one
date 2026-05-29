import type React from 'react';

export function SectionTitle({ children, count }: { children: React.ReactNode; count?: number }) {
	return (
		<h3 className="text-muted-foreground mb-2 flex items-baseline gap-2 text-xs font-medium tracking-wider uppercase">
			{children}
			{count != null && (
				<span className="text-muted-foreground/60 font-mono text-[10px] normal-case">
					{count}
				</span>
			)}
		</h3>
	);
}
