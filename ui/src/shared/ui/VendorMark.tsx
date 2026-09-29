/**
 * VendorMark — a brand mark for one of the few APIs the Agents landing uses in
 * its story (see `vendorMarks.ts` for the source, licence and trademark note).
 *
 * The mark is drawn in the brand's own colour on a light tile: several brand
 * colours are near-black (GitHub, Notion), so on the app's dark surfaces the
 * tile is what keeps each mark legible and true to its brand. An unknown slug
 * falls back to a neutral initial tile, so a caller never renders nothing.
 *
 * Decorative by default (the API's name sits beside it); pass `label` when the
 * mark stands alone.
 */
import { cn } from '@/shared/lib/utils';
import { VENDOR_MARKS, isVendorMarkSlug } from '@/shared/ui/vendorMarks';

export type VendorMarkSize = 'xs' | 'sm' | 'md' | 'lg';

const TILE: Record<VendorMarkSize, string> = {
	xs: 'h-4 w-4 rounded-[4px]',
	sm: 'h-5 w-5 rounded-md',
	md: 'h-7 w-7 rounded-md',
	lg: 'h-9 w-9 rounded-lg',
};

const GLYPH: Record<VendorMarkSize, string> = {
	xs: 'h-2.5 w-2.5',
	sm: 'h-3 w-3',
	md: 'h-4 w-4',
	lg: 'h-5 w-5',
};

const INITIAL_TEXT: Record<VendorMarkSize, string> = {
	xs: 'text-[8px]',
	sm: 'text-[9px]',
	md: 'text-[11px]',
	lg: 'text-xs',
};

export interface VendorMarkProps {
	/** A `vendorMarks` slug (`github`, `gmail`, …); anything else renders an initial. */
	slug: string;
	size?: VendorMarkSize;
	/** Accessible name when the mark stands alone; omitted → decorative. */
	label?: string;
	className?: string;
}

export function VendorMark({ slug, size = 'sm', label, className }: VendorMarkProps) {
	const a11y = label
		? ({ role: 'img', 'aria-label': label } as const)
		: ({ 'aria-hidden': true } as const);
	if (!isVendorMarkSlug(slug)) {
		return (
			<span
				{...a11y}
				data-vendor-mark="fallback"
				className={cn(
					'bg-muted text-muted-foreground ring-border inline-flex shrink-0 items-center justify-center font-mono font-semibold uppercase ring-1',
					TILE[size],
					INITIAL_TEXT[size],
					className,
				)}
			>
				{slug.slice(0, 1)}
			</span>
		);
	}
	const mark = VENDOR_MARKS[slug];
	return (
		<span
			{...a11y}
			data-vendor-mark={slug}
			className={cn(
				// A light tile is the one fixed surface here: brand marks are designed
				// for a light ground, whichever theme the app is in.
				'ring-border inline-flex shrink-0 items-center justify-center bg-white ring-1',
				TILE[size],
				className,
			)}
		>
			<svg
				xmlns="http://www.w3.org/2000/svg"
				viewBox="0 0 24 24"
				className={GLYPH[size]}
				fill={mark.hex}
				aria-hidden="true"
				focusable="false"
			>
				<path d={mark.path} />
			</svg>
		</span>
	);
}
