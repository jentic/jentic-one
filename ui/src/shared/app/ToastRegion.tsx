/**
 * ToastRegion — where both toasters appear: the agent-stream `ToastHost` and the
 * platform `Toaster`, stacked in one column.
 *
 * Bottom-right, beside whatever covers the right edge (the agent rail at `xl+`,
 * an open right-hand sheet), so a toast never lands on a sheet's footer actions.
 * It sits as high as `FOOTER_ACTION_BAR_PAGE_PADDING`, clearing a page's
 * floating action dock and, below `md`, the bottom nav. When a sheet leaves no
 * room beside it — a full-width sheet on a phone — the toasts drop in under the
 * sheet's header instead, away from its footer.
 */
import { ToastHost } from '@/shared/app/rail/ToastHost';
import { useMediaQuery } from '@/shared/hooks/useMediaQuery';
import { cn } from '@/shared/lib/utils';
import { useRightEdgeInset } from '@/shared/ui/rightEdge';
import { Toaster } from '@/shared/ui/Toaster';

/** The region's width (`w-96`) and its gap to the viewport or a cover's edge. */
const TOAST_WIDTH_PX = 384;
const EDGE_GAP_PX = 16;

export function ToastRegion() {
	const inset = useRightEdgeInset();
	const roomBeside = useMediaQuery(`(min-width: ${inset + TOAST_WIDTH_PX + 2 * EDGE_GAP_PX}px)`);
	const beside = inset === 0 || roomBeside;

	return (
		<div
			data-testid="toast-region"
			data-placement={beside ? 'beside' : 'top'}
			className={cn(
				'pointer-events-none fixed z-[60] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2',
				beside
					? 'bottom-[calc(9rem+env(safe-area-inset-bottom))] md:bottom-24'
					: 'inset-x-0 top-20 mx-auto',
			)}
			style={beside ? { right: inset + EDGE_GAP_PX } : undefined}
		>
			<ToastHost />
			<Toaster />
		</div>
	);
}
