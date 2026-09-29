/**
 * AgentMark — "your AI agent", whichever one it is: a 2×2 grid of the AI
 * agent / assistant marks (Claude, OpenAI, Cursor, Google Gemini), each on its
 * own small rounded light tile, with no container around them. Source, licence and trademark note live in `vendorMarks.ts`.
 *
 * Decorative by default; pass `label` when it stands alone.
 */
import { cn } from '@/shared/lib/utils';
import { AGENT_MARK_SLUGS, VENDOR_MARKS } from '@/shared/ui/vendorMarks';

export type AgentMarkSize = 'sm' | 'md';

const GRID: Record<AgentMarkSize, string> = {
	sm: 'gap-0.5',
	md: 'gap-1',
};

const TILE: Record<AgentMarkSize, string> = {
	sm: 'h-4 w-4 rounded-[4px]',
	md: 'h-7 w-7 rounded-lg',
};

const GLYPH: Record<AgentMarkSize, string> = {
	sm: 'h-2.5 w-2.5',
	md: 'h-4 w-4',
};

export interface AgentMarkProps {
	size?: AgentMarkSize;
	label?: string;
	className?: string;
}

export function AgentMark({ size = 'sm', label, className }: AgentMarkProps) {
	const a11y = label
		? ({ role: 'img', 'aria-label': label } as const)
		: ({ 'aria-hidden': true } as const);
	return (
		<span
			{...a11y}
			data-agent-mark=""
			className={cn('grid shrink-0 grid-cols-2', GRID[size], className)}
		>
			{AGENT_MARK_SLUGS.map((slug) => {
				const mark = VENDOR_MARKS[slug];
				return (
					<span
						key={slug}
						data-vendor-mark={slug}
						// A light tile: brand marks are designed for a light ground.
						className={cn(
							'ring-border/60 inline-flex items-center justify-center bg-white ring-1',
							TILE[size],
						)}
					>
						<svg viewBox="0 0 24 24" className={GLYPH[size]} aria-hidden="true">
							<path d={mark.path} fill={mark.hex} />
						</svg>
					</span>
				);
			})}
		</span>
	);
}
