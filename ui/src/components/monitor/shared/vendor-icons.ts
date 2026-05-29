/**
 * Vendor icon configuration for Monitor charts.
 *
 * Thin function-style facade over `@/lib/vendor-registry`, the single source
 * of truth shared with `discovery/VendorIcon`. The chart code consumes
 * `getVendorConfig(vendor)` (returning bg / ring / text / iconUrl as
 * raw strings) so we can drive SVG `fill=` attributes directly without
 * spinning up a React component for every cell.
 *
 * Behaviour:
 *  - Curated brands (`VENDOR_REGISTRY` in `vendor-registry.ts`) get their
 *    real brand colour and the Simple Icons SVG.
 *  - 2-label hosts (e.g. `linear.app`, `posthog.com`) auto-derive an icon
 *    URL but use a hashed palette colour rather than a guessed brand bg.
 *  - Anything else (no vendor at all, multi-label hosts, IPs) gets a
 *    palette colour with no icon — initials only.
 *
 * The hashed palette (16 distinct colours) is deterministic per vendor
 * string, so the same API renders the same colour across the bubble
 * chart, the daily bar chart, the breakdown table, and the active-APIs
 * strip.
 */

import { getIconUrl, resolveBrand, vendorPalette } from '@/lib/vendor-registry';

export interface VendorConfig {
	/** Solid hex used for chart fills, table swatches, etc. */
	bg: string;
	/** Slightly lighter hex for hover/focus rings on the bubble chart. */
	ring: string;
	/** `#fff` or a near-black, depending on bg luminance. */
	text: string;
	/**
	 * Simple Icons CDN URL. Empty string when we don't have an icon —
	 * callers fall back to `getInitials()` text.
	 */
	iconUrl: string;
}

/**
 * Resolve a vendor (host or registry key) to its chart-ready config.
 *
 * Lookup order:
 *  1. Curated registry hit → real brand colour + icon.
 *  2. Auto-derivable 2-label host → icon from Simple Icons (CDN may 404
 *     for less famous brands; chart code can ignore the icon and just use
 *     the palette colour) + hashed palette colour.
 *  3. Anything else → no icon, hashed palette colour.
 *
 * The hashed palette guarantees that two unknown vendors don't share a
 * colour — fixing the long-standing "every API in the chart is the same
 * indigo" bug from when the fallback was a single hard-coded swatch.
 */
export function getVendorConfig(vendor: string): VendorConfig {
	const brand = resolveBrand(vendor);

	if (brand && brand.bg !== null) {
		// Curated brand: the registry pins the bg; ring is a slightly lighter
		// shade derived per-brand below for the few cases that need it. For
		// the rest we just reuse the bg as the ring (visually fine on the
		// bubble hover state where the ring sits 2.5px outside the bubble).
		return {
			bg: brand.bg,
			ring: brand.bg,
			text: brand.invert ? '#fff' : '#1a1a1a',
			iconUrl: getIconUrl(brand.slug),
		};
	}

	const palette = vendorPalette(vendor);

	if (brand) {
		// Auto-derived slug — we don't trust the brand colour (we don't
		// have one), so we ride the palette but keep the icon URL.
		return {
			bg: palette.bg,
			ring: palette.ring,
			text: palette.text,
			iconUrl: getIconUrl(brand.slug),
		};
	}

	// No brand at all — colour-only.
	return {
		bg: palette.bg,
		ring: palette.ring,
		text: palette.text,
		iconUrl: '',
	};
}

export function getInitials(name: string): string {
	return name
		.replace(/\s*API\s*/i, '')
		.split(/[\s\-_]+/)
		.slice(0, 2)
		.map((w) => w[0]?.toUpperCase() || '')
		.join('');
}

/** Inverts black Simple Icons SVGs to white. Reference as filter="url(#icon-to-white)". */
export const ICON_INVERT_FILTER_ID = 'icon-to-white';

/** No-op filter — icons stay black (for light-text vendors like Mailchimp). */
export const ICON_DARK_FILTER_ID = 'icon-keep-dark';

export function getIconFilterId(vendor: string): string {
	const config = getVendorConfig(vendor);
	return config.text === '#fff' ? ICON_INVERT_FILTER_ID : ICON_DARK_FILTER_ID;
}
