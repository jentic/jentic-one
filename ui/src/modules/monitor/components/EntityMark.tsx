/**
 * EntityMark — the small pastel initials tile Monitor's chart legends and
 * tooltips use as a series key. It is the avatar mark (`VendorIcon` /
 * `AgentBadge` grammar: flat pastel tile, dark same-hue initials, Sora 700)
 * drawn in the tone the chart assigned, so if a hue collision moved a series
 * to its fallback tone, its legend chip and tooltip row move with it.
 *
 * Decorative: the entity's name is always printed beside it.
 */
import { cn } from '@/shared/lib/utils';
import { entityInitials, type EntityTone, type UsageLens } from '@/modules/monitor/lib/palette';

export function EntityMark({
	tone,
	label,
	lens,
	className,
}: {
	tone: EntityTone;
	label: string;
	lens: UsageLens;
	className?: string;
}) {
	return (
		<span
			aria-hidden="true"
			data-testid="entity-mark"
			data-tone={tone.tone ?? 'neutral'}
			className={cn(
				'font-heading grid h-4 w-4 shrink-0 place-items-center rounded-[5px] text-[7px] leading-none font-bold tracking-[0.01em] uppercase select-none',
				className,
			)}
			style={{ backgroundColor: tone.tile, color: tone.ink }}
		>
			{label === 'Other' && tone.tone == null ? '+' : entityInitials(lens, label)}
		</span>
	);
}
