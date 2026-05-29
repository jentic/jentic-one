/**
 * Vendor icon — real brand logo when we recognise the vendor domain, with a
 * deterministic gradient + initials fallback otherwise.
 *
 * Brand registry, slug derivation, and colour palette all live in
 * `@/lib/vendor-registry` so the Monitor charts and this Discover icon
 * agree on which logo + colour belong to which vendor. See that module's
 * header for the resolution order and gradient/palette rules.
 *
 * Tests in `__tests__/components/VendorIcon.test.tsx` only exercise the
 * fallback path (they don't pass `vendor`).
 */

import { useState } from 'react';
import {
	findVendorSuggestions as registryFindVendorSuggestions,
	getIconUrl,
	hashStr,
	KNOWN_VENDORS as REGISTRY_KNOWN_VENDORS,
	resolveBrand,
} from '@/lib/vendor-registry';

/** Re-exported so callers using `@/components/discovery/VendorIcon` keep their imports. */
export const KNOWN_VENDORS = REGISTRY_KNOWN_VENDORS;

/** Re-exported convenience wrapper, identical to `vendorRegistry.findVendorSuggestions`. */
export function findVendorSuggestions(query: string, limit = 3): string[] {
	return registryFindVendorSuggestions(query, limit);
}

/**
 * Module-level cache of slugs that 404'd on the CDN. Lets sibling cards
 * skip a known-bad probe instead of every card firing its own request.
 */
const failedSlugs = new Set<string>();

const GRADIENTS = [
	'from-blue-500 to-blue-600',
	'from-emerald-500 to-emerald-600',
	'from-violet-500 to-violet-600',
	'from-orange-500 to-orange-600',
	'from-pink-500 to-pink-600',
	'from-teal-500 to-teal-600',
	'from-indigo-500 to-indigo-600',
	'from-cyan-500 to-cyan-600',
	'from-rose-500 to-rose-600',
	'from-amber-500 to-amber-600',
] as const;

function getGradient(seed: string): string {
	return GRADIENTS[hashStr(seed) % GRADIENTS.length];
}

function getInitials(name: string): string {
	return (
		name
			.replace(/[^a-z0-9]/gi, '')
			.slice(0, 2)
			.toUpperCase() || '??'
	);
}

type IconSize = 'sm' | 'md' | 'lg';

/**
 * Sizes are intentionally smaller than `jentic-webapp` (32 / 40 / 48 vs
 * 40 / 48 / 64) because Discover cards in mini are tighter. To still match
 * the webapp's *visual* roundness we hold the radius:side ratio at ~25 %
 * via arbitrary values — `rounded-xl` (12 px) on our 40 px md icon would
 * read at 30 %, more "blobby" than webapp's 25 %.
 */
const SIZE: Record<IconSize, { box: string; radius: string; img: string; text: string }> = {
	sm: { box: 'h-8 w-8', radius: 'rounded-[8px]', img: 'h-3.5 w-3.5', text: 'text-[11px]' },
	md: { box: 'h-10 w-10', radius: 'rounded-[10px]', img: 'h-4 w-4', text: 'text-sm' },
	lg: { box: 'h-12 w-12', radius: 'rounded-xl', img: 'h-5 w-5', text: 'text-base' },
};

export function VendorIcon({
	name,
	vendor,
	size = 'md',
	className,
}: {
	/** Human-readable name used for initials fallback and `alt` text. */
	name: string;
	/**
	 * Vendor / domain key used to look up a brand logo
	 * (e.g. `stripe.com`, `github.com`). Optional — when omitted or unknown
	 * we fall back to a gradient + initials icon.
	 */
	vendor?: string;
	size?: IconSize;
	className?: string;
}) {
	const brand = resolveBrand(vendor);
	const cachedFail = brand ? failedSlugs.has(brand.slug) : false;
	const [imgFailed, setImgFailed] = useState(cachedFail);
	const { box, radius, img, text } = SIZE[size];
	const seed = vendor ?? name;

	if (brand && !imgFailed) {
		// `bg=null` ⇒ auto-derived slug; tint with the same hashed gradient
		// the fallback uses so unknown brands still get colour variety.
		const useGradient = brand.bg === null;
		const gradient = useGradient ? getGradient(seed) : '';
		const containerClass = `flex shrink-0 items-center justify-center shadow-sm ${box} ${radius} ${
			useGradient ? `bg-gradient-to-br ${gradient}` : ''
		} ${className ?? ''}`;

		return (
			<div
				className={containerClass}
				style={useGradient ? undefined : { backgroundColor: brand.bg ?? undefined }}
			>
				<img
					src={getIconUrl(brand.slug)}
					alt={name}
					className={img}
					style={{ filter: brand.invert ? 'brightness(0) invert(1)' : 'none' }}
					onError={() => {
						failedSlugs.add(brand.slug);
						setImgFailed(true);
					}}
				/>
			</div>
		);
	}

	const gradient = getGradient(seed);
	const initials = getInitials(name);

	return (
		<div
			className={`flex shrink-0 items-center justify-center bg-gradient-to-br font-semibold text-white shadow-sm ${gradient} ${box} ${radius} ${text} ${className ?? ''}`}
			aria-hidden="true"
		>
			{initials}
		</div>
	);
}
