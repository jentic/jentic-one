/**
 * Vendor icon — real brand logo when we recognise the vendor domain, with a
 * deterministic gradient + initials fallback otherwise.
 *
 * Ported from `jentic-webapp` (`client/src/components/workflows/VendorIcon.tsx`)
 * so Discover surface icons match the design language of the hosted product.
 * Size scale + radius (`rounded-xl`) match webapp exactly so visual radius
 * proportions read identically (12px on a 48px icon, not on a 40px one).
 *
 * Resolution order (3-tier hybrid — see chat decision May 2026):
 *
 *   1. VENDOR_REGISTRY override          — exact brand colour + curated slug.
 *                                          Use this when the auto-derived slug
 *                                          would be wrong (Atlassian → jira,
 *                                          Google → googlesheets) or when we
 *                                          want a non-default brand colour.
 *   2. deriveSlugFromDomain(vendor)      — auto-strip `api.`/`www.`/`vN.` and
 *                                          take the SLD (`stripe.com` → stripe).
 *                                          Renders the SVG over a deterministic
 *                                          gradient bg with white invert so we
 *                                          don't need to hand-curate brand
 *                                          colours for every API.
 *   3. gradient + initials               — final fallback (404 from CDN, no
 *                                          vendor at all, non-2-label hosts
 *                                          like `0xerr0r.github.io` where the
 *                                          SLD is meaningless).
 *
 * Slugs come from Simple Icons v13 (~3,300 brand SVGs), served via jsDelivr.
 * Failed lookups are cached in `failedSlugs` so we don't re-probe known-bad
 * slugs on every re-render.
 *
 * Tests in `__tests__/components/VendorIcon.test.tsx` only exercise the
 * fallback path (they don't pass `vendor`) — keep the initials algorithm
 * stable there.
 */

import { useState } from 'react';

interface BrandEntry {
	slug: string;
	bg: string;
	/** True when the SVG should be inverted to white (dark brand colours). */
	textWhite: boolean;
}

/* eslint-disable prettier/prettier */
const VENDOR_REGISTRY: Record<string, BrandEntry> = {
	'slack.com':     { slug: 'slack',        bg: '#4A154B', textWhite: true },
	'github.com':    { slug: 'github',       bg: '#24292f', textWhite: true },
	'stripe.com':    { slug: 'stripe',       bg: '#635BFF', textWhite: true },
	'sendgrid.com':  { slug: 'sendgrid',     bg: '#1A82E2', textWhite: true },
	'twilio.com':    { slug: 'twilio',       bg: '#F22F46', textWhite: true },
	'notion.so':     { slug: 'notion',       bg: '#000000', textWhite: true },
	'airtable.com':  { slug: 'airtable',     bg: '#18BFFF', textWhite: true },
	'openai.com':    { slug: 'openai',       bg: '#412991', textWhite: true },
	'google.com':    { slug: 'googlesheets', bg: '#0F9D58', textWhite: true },
	'atlassian.com': { slug: 'jira',         bg: '#0052CC', textWhite: true },
	'hubspot.com':   { slug: 'hubspot',      bg: '#FF7A59', textWhite: true },
	'zoom.us':       { slug: 'zoom',         bg: '#0B5CFF', textWhite: true },
	'mailchimp.com': { slug: 'mailchimp',    bg: '#FFE01B', textWhite: false },
	'zendesk.com':   { slug: 'zendesk',      bg: '#03363D', textWhite: true },
	'discord.com':   { slug: 'discord',      bg: '#5865F2', textWhite: true },
	'asana.com':     { slug: 'asana',        bg: '#F06A6A', textWhite: true },
	'linear.app':    { slug: 'linear',       bg: '#5E6AD2', textWhite: true },
	'figma.com':     { slug: 'figma',        bg: '#F24E1E', textWhite: true },
	'shopify.com':   { slug: 'shopify',      bg: '#7AB55C', textWhite: true },
	'segment.com':   { slug: 'segment',      bg: '#52BD95', textWhite: true },
	'cloudflare.com':{ slug: 'cloudflare',   bg: '#F38020', textWhite: true },
	'aws.amazon.com':{ slug: 'amazonaws',    bg: '#232F3E', textWhite: true },
	'gitlab.com':    { slug: 'gitlab',       bg: '#FC6D26', textWhite: true },
};
/* eslint-enable prettier/prettier */

const SUBDOMAIN_STRIP_RE = /^(api|www|v\d+)\./;
/** Matches an SLD.TLD pair, e.g. `stripe.com`, `linear.app`. NOT `0xerr0r.github.io`. */
const TWO_LABEL_HOST_RE = /^([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)\.[a-z]{2,}$/;
/** Simple Icons slug rules: lowercase alphanum + hyphen, no leading/trailing dash. */
const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/;

function normaliseVendor(vendor: string): string {
	return vendor.toLowerCase().trim().replace(SUBDOMAIN_STRIP_RE, '');
}

/**
 * Vendor IDs (registry keys) exposed for callers that need to suggest
 * matching vendors for a free-form query — e.g. the Discover zero-search
 * empty state. Lowercased domain form (`stripe.com`, `github.com`).
 */
export const KNOWN_VENDORS: readonly string[] = Object.keys(VENDOR_REGISTRY);

/**
 * Up to `limit` vendor IDs whose registry key contains the query (case-
 * insensitive substring). Matches against both the full key (`stripe.com`)
 * and the SLD (`stripe`) so users typing "strpie"/"stripe"/"stripe.com"
 * land on the same row. Only emits matches when `query.length >= 2` to
 * avoid pathological all-vendor suggestions on a single character.
 */
export function findVendorSuggestions(query: string, limit = 3): string[] {
	const q = query.toLowerCase().trim();
	if (q.length < 2) return [];
	const out: string[] = [];
	for (const vendor of KNOWN_VENDORS) {
		const sld = vendor.split('.')[0] ?? vendor;
		if (vendor.includes(q) || sld.includes(q)) {
			out.push(vendor);
			if (out.length >= limit) break;
		}
	}
	return out;
}

/**
 * Match a free-form vendor / API id against the curated registry. Tries the
 * raw value first, then the prefix-stripped variant, so `api.stripe.com`
 * still wins the Stripe entry.
 */
function lookupRegistry(vendor: string): BrandEntry | null {
	const raw = vendor.toLowerCase().trim();
	if (VENDOR_REGISTRY[raw]) return VENDOR_REGISTRY[raw];
	const stripped = raw.replace(SUBDOMAIN_STRIP_RE, '');
	return VENDOR_REGISTRY[stripped] ?? null;
}

/**
 * Best-effort Simple Icons slug from a domain. Returns null for hosts that
 * don't look like a 2-label SLD.TLD pair — for those, the SLD is rarely a
 * meaningful brand (`0xerr0r.github.io` should NOT render the GitHub logo).
 */
function deriveSlugFromDomain(vendor: string): string | null {
	const host = normaliseVendor(vendor);
	const match = host.match(TWO_LABEL_HOST_RE);
	if (!match) return null;
	const slug = match[1].replace(/-/g, ''); // simple-icons strips dashes (e.g. send-grid → sendgrid)
	return SLUG_RE.test(slug) ? slug : null;
}

interface ResolvedBrand {
	slug: string;
	/** Hex background. `null` means "use a deterministic gradient instead". */
	bg: string | null;
	/** Invert the SVG to white when true. */
	invert: boolean;
}

function resolveBrand(vendor: string | undefined): ResolvedBrand | null {
	if (!vendor) return null;
	const reg = lookupRegistry(vendor);
	if (reg) return { slug: reg.slug, bg: reg.bg, invert: reg.textWhite };
	const auto = deriveSlugFromDomain(vendor);
	// Auto-derived brands ride on a hashed gradient (bg: null) so we don't
	// have to hand-curate brand colours for the long tail of APIs.
	if (auto) return { slug: auto, bg: null, invert: true };
	return null;
}

/**
 * Module-level cache of slugs that 404'd on the CDN. Lets sibling cards
 * skip a known-bad probe instead of every card firing its own request.
 */
const failedSlugs = new Set<string>();

function getIconUrl(slug: string): string {
	return `https://cdn.jsdelivr.net/npm/simple-icons@v13/icons/${slug}.svg`;
}

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

function hashStr(s: string): number {
	let h = 0;
	for (let i = 0; i < s.length; i++) {
		h = s.charCodeAt(i) + ((h << 5) - h);
	}
	return Math.abs(h);
}

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

	// Final fallback: gradient + initials. Seed off vendor when present so the
	// same provider keeps the same colour even if its display name changes.
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
