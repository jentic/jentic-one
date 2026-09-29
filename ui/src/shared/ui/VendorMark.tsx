/**
 * VendorMark — the brand mark of an API the Agents landing names (see
 * `vendorMarks.ts` for the set, its source, licence and trademark note).
 *
 * The mark is drawn in the brand's own colour on a light tile: brand colours
 * can be near-black (GitHub's is), so on the app's dark surfaces the tile is
 * what keeps the mark legible and true to its brand. An unknown slug falls back
 * to a neutral initial tile, so a caller never renders nothing.
 *
 * Decorative by default (the API's name sits beside it); pass `label` when the
 * mark stands alone.
 */
import { cn } from '@/shared/lib/utils';
import { VENDOR_MARKS, isVendorMarkSlug } from '@/shared/ui/vendorMarks';

export type VendorMarkSize = 'sm' | 'md';

const TILE: Record<VendorMarkSize, string> = {
	sm: 'h-5 w-5 rounded-md',
	md: 'h-7 w-7 rounded-md',
};

const GLYPH: Record<VendorMarkSize, string> = {
	sm: 'h-3 w-3',
	md: 'h-4 w-4',
};

const INITIAL_TEXT: Record<VendorMarkSize, string> = {
	sm: 'text-[9px]',
	md: 'text-[11px]',
};

export interface VendorMarkProps {
	/** A `vendorMarks` slug (`github`, …); anything else renders an initial. */
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
