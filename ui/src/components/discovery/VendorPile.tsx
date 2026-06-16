import { VendorIcon } from './VendorIcon';
import { cn } from '@/lib/utils';

const DEFAULT_MAX = 4;

export interface VendorPileProps {
	/** API / vendor ids (e.g. `stripe.com`, `github.com`). Order is preserved. */
	vendors: string[];
	/** Max icons shown before collapsing the rest into a `+N` chip. Default 4. */
	max?: number;
	/** Icon size token forwarded to `VendorIcon`. Default `sm` (32px). */
	size?: 'sm' | 'md' | 'lg';
	/**
	 * Accessible summary. Defaults to a generic "N APIs: …" sentence; pass a
	 * domain-specific label (e.g. "Touches 3 APIs: …") where it reads better.
	 */
	ariaLabel?: string;
	className?: string;
	/** Optional test id forwarded to the container. */
	testId?: string;
}

/**
 * Compact pile of vendor logos with a `+N` overflow chip.
 *
 * Extracted from the workspace workflow tile so the same affordance can be
 * reused anywhere we want to hint at "which APIs this thing touches" — e.g.
 * toolkit cards (fed by the toolkit's bound credentials' `api_id`s) and
 * workflow tiles (fed by `involved_apis`). Each id is passed as both `name`
 * and `vendor` to `VendorIcon`, which resolves a brand logo for recognized
 * domains and falls back to a hashed gradient + initials otherwise.
 */
export function VendorPile({
	vendors,
	max = DEFAULT_MAX,
	size = 'sm',
	ariaLabel,
	className,
	testId,
}: VendorPileProps) {
	if (vendors.length === 0) return null;
	const visible = vendors.slice(0, max);
	const overflow = vendors.length - visible.length;
	return (
		<div
			className={cn('flex items-center gap-1.5', className)}
			data-testid={testId}
			aria-label={
				ariaLabel ??
				`${vendors.length} API${vendors.length === 1 ? '' : 's'}: ${vendors.join(', ')}`
			}
		>
			{visible.map((v) => (
				<span key={v} title={v} className="inline-flex">
					<VendorIcon name={v} vendor={v} size={size} />
				</span>
			))}
			{overflow > 0 ? (
				<span className="text-muted-foreground bg-muted/60 inline-flex h-6 min-w-6 items-center justify-center rounded-md px-1 text-[10px] font-medium">
					+{overflow}
				</span>
			) : null}
		</div>
	);
}
