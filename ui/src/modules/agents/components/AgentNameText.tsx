/**
 * AgentNameText — an agent's name inside running copy ("Give my-first-agent its
 * first API"). Names can run to 255 characters, so a sentence never inlines
 * one whole: past its budget the name ends in an ellipsis and the full name
 * shows in a tooltip — only when it was actually cut. The budget fits the
 * suggested `my-first-agent` (14ch) with room to spare.
 */
import { TruncateWithTooltip } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';

/** How much of a sentence an agent's name may take before it truncates. */
const AGENT_NAME_INLINE_BUDGET = 'max-w-[24ch]';

export function AgentNameText({
	name,
	className,
	focusable,
}: {
	name: string;
	className?: string;
	/** False inside a control whose accessible name already carries the name. */
	focusable?: boolean;
}) {
	return (
		<TruncateWithTooltip
			inline
			focusable={focusable}
			className={cn(AGENT_NAME_INLINE_BUDGET, className)}
		>
			{name}
		</TruncateWithTooltip>
	);
}
