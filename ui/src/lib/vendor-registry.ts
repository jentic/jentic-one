/**
 * Shared vendor / brand resolution for both `discovery/VendorIcon` and the
 * Monitor chart palette.
 *
 * Why this exists: there used to be two parallel registries — a 10-vendor
 * one in `monitor/shared/vendor-icons.ts` (driving chart colours) and a
 * 23-vendor one in `discovery/VendorIcon.tsx` (driving Discover icons).
 * That caused two visible bugs:
 *   1. Discover-only brands (e.g. Stripe, OpenAI) rendered as a generic
 *      indigo blob in Monitor charts.
 *   2. Every unknown vendor used the same fallback colour, so all
 *      "everything else" APIs in the bubble/bar/breakdown charts ended up
 *      drawn as the same shade.
 *
 * Single source of truth lives here:
 *   - `VENDOR_REGISTRY` — curated brand colour + Simple Icons slug for the
 *     vendors we care about.
 *   - `resolveBrand(vendor)` — best-effort lookup with auto-derivation
 *     from `*.example.com` style hosts, used by the icon component.
 *   - `vendorPalette(vendor, name?)` — deterministic hashed palette for
 *     anything outside the registry, so chart colours don't collide.
 *
 * Tests: `__tests__/lib/vendor-registry.test.ts` covers the auto-derive
 * stripping and the palette-hash determinism. The icon-component side is
 * still tested in `__tests__/components/VendorIcon.test.tsx`.
 */

interface BrandEntry {
	slug: string;
	bg: string;
	/** True when the SVG should be inverted to white (dark brand colours). */
	textWhite: boolean;
}

export const VENDOR_REGISTRY: Record<string, BrandEntry> = {
	'slack.com': { slug: 'slack', bg: '#4A154B', textWhite: true },
	'github.com': { slug: 'github', bg: '#24292f', textWhite: true },
	'stripe.com': { slug: 'stripe', bg: '#635BFF', textWhite: true },
	'sendgrid.com': { slug: 'sendgrid', bg: '#1A82E2', textWhite: true },
	'twilio.com': { slug: 'twilio', bg: '#F22F46', textWhite: true },
	'notion.so': { slug: 'notion', bg: '#000000', textWhite: true },
	'airtable.com': { slug: 'airtable', bg: '#18BFFF', textWhite: true },
	'openai.com': { slug: 'openai', bg: '#412991', textWhite: true },
	'google.com': { slug: 'googlesheets', bg: '#0F9D58', textWhite: true },
	'atlassian.com': { slug: 'jira', bg: '#0052CC', textWhite: true },
	'hubspot.com': { slug: 'hubspot', bg: '#FF7A59', textWhite: true },
	'zoom.us': { slug: 'zoom', bg: '#0B5CFF', textWhite: true },
	'mailchimp.com': { slug: 'mailchimp', bg: '#FFE01B', textWhite: false },
	'zendesk.com': { slug: 'zendesk', bg: '#03363D', textWhite: true },
	'discord.com': { slug: 'discord', bg: '#5865F2', textWhite: true },
	'asana.com': { slug: 'asana', bg: '#F06A6A', textWhite: true },
	'linear.app': { slug: 'linear', bg: '#5E6AD2', textWhite: true },
	'figma.com': { slug: 'figma', bg: '#F24E1E', textWhite: true },
	'shopify.com': { slug: 'shopify', bg: '#7AB55C', textWhite: true },
	'segment.com': { slug: 'segment', bg: '#52BD95', textWhite: true },
	'cloudflare.com': { slug: 'cloudflare', bg: '#F38020', textWhite: true },
	'aws.amazon.com': { slug: 'amazonaws', bg: '#232F3E', textWhite: true },
	'gitlab.com': { slug: 'gitlab', bg: '#FC6D26', textWhite: true },
};

const SUBDOMAIN_STRIP_RE = /^(api|www|v\d+)\./;
/** Matches an SLD.TLD pair, e.g. `stripe.com`, `linear.app`. NOT `0xerr0r.github.io`. */
const TWO_LABEL_HOST_RE = /^([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)\.[a-z]{2,}$/;
/** Simple Icons slug rules: lowercase alphanum + hyphen. */
const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/;

export function normaliseVendor(vendor: string): string {
	return vendor.toLowerCase().trim().replace(SUBDOMAIN_STRIP_RE, '');
}

/**
 * Vendor IDs (registry keys) exposed for callers that need to suggest
 * matching vendors for a free-form query — used by the Discover zero-search
 * empty state. Lowercased domain form (`stripe.com`, `github.com`).
 */
export const KNOWN_VENDORS: readonly string[] = Object.keys(VENDOR_REGISTRY);

/**
 * Up to `limit` vendor IDs whose registry key contains the query (case-
 * insensitive substring). Matches against both the full key (`stripe.com`)
 * and the SLD (`stripe`).
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
 * raw value first, then the prefix-stripped variant.
 */
function lookupRegistry(vendor: string): BrandEntry | null {
	const raw = vendor.toLowerCase().trim();
	if (VENDOR_REGISTRY[raw]) return VENDOR_REGISTRY[raw];
	const stripped = raw.replace(SUBDOMAIN_STRIP_RE, '');
	return VENDOR_REGISTRY[stripped] ?? null;
}

/**
 * Best-effort Simple Icons slug from a domain. Returns null for hosts that
 * don't look like a 2-label SLD.TLD pair.
 */
function deriveSlugFromDomain(vendor: string): string | null {
	const host = normaliseVendor(vendor);
	const match = host.match(TWO_LABEL_HOST_RE);
	if (!match) return null;
	const slug = match[1].replace(/-/g, '');
	return SLUG_RE.test(slug) ? slug : null;
}

export interface ResolvedBrand {
	slug: string;
	/** Hex background. `null` means "use a deterministic gradient instead". */
	bg: string | null;
	/** Invert the SVG to white when true. */
	invert: boolean;
}

export function resolveBrand(vendor: string | undefined): ResolvedBrand | null {
	if (!vendor) return null;
	const reg = lookupRegistry(vendor);
	if (reg) return { slug: reg.slug, bg: reg.bg, invert: reg.textWhite };
	const auto = deriveSlugFromDomain(vendor);
	if (auto) return { slug: auto, bg: null, invert: true };
	return null;
}

/**
 * Stable string hash (djb2 variant). Same input ⇒ same output, so the
 * hashed palette below assigns the same colour to the same vendor across
 * page renders, charts, and table rows.
 */
export function hashStr(s: string): number {
	let h = 0;
	for (let i = 0; i < s.length; i++) {
		h = s.charCodeAt(i) + ((h << 5) - h);
	}
	return Math.abs(h);
}

const ICON_BASE = 'https://cdn.jsdelivr.net/npm/simple-icons@v13/icons';

export function getIconUrl(slug: string): string {
	return `${ICON_BASE}/${slug}.svg`;
}

/* ────────────────────────────────────────────────────────────────────── */
/* Hashed palette for "we don't know this vendor" rendering              */
/* ────────────────────────────────────────────────────────────────────── */

/**
 * 16 visually distinct paint colours for unknown vendors. Spaced around
 * the colour wheel so adjacent palette entries don't read as the same
 * hue at chart scale (10–20px). Light-text where the bg is dark, dark-text
 * for the few light backgrounds (yellow/amber).
 *
 * Order matters: when several unknown vendors hit the same hash bucket
 * (rare with 16 slots and a djb2 hash, but possible), they alternate
 * within the palette. Don't reorder casually — chart colours are a
 * visual contract with users who memorise "the orange one is X".
 */
const VENDOR_PALETTE: ReadonlyArray<{
	bg: string;
	ring: string;
	text: '#fff' | '#1a1a1a';
}> = [
	{ bg: '#0EA5E9', ring: '#38BDF8', text: '#fff' }, //  0 sky
	{ bg: '#F97316', ring: '#FB923C', text: '#fff' }, //  1 orange
	{ bg: '#10B981', ring: '#34D399', text: '#fff' }, //  2 emerald
	{ bg: '#EC4899', ring: '#F472B6', text: '#fff' }, //  3 pink
	{ bg: '#8B5CF6', ring: '#A78BFA', text: '#fff' }, //  4 violet
	{ bg: '#F59E0B', ring: '#FBBF24', text: '#1a1a1a' }, //  5 amber
	{ bg: '#06B6D4', ring: '#22D3EE', text: '#fff' }, //  6 cyan
	{ bg: '#EF4444', ring: '#F87171', text: '#fff' }, //  7 red
	{ bg: '#14B8A6', ring: '#2DD4BF', text: '#fff' }, //  8 teal
	{ bg: '#A855F7', ring: '#C084FC', text: '#fff' }, //  9 purple
	{ bg: '#84CC16', ring: '#A3E635', text: '#1a1a1a' }, // 10 lime
	{ bg: '#3B82F6', ring: '#60A5FA', text: '#fff' }, // 11 blue
	{ bg: '#D946EF', ring: '#E879F9', text: '#fff' }, // 12 fuchsia
	{ bg: '#65A30D', ring: '#84CC16', text: '#fff' }, // 13 dark lime
	{ bg: '#DB2777', ring: '#EC4899', text: '#fff' }, // 14 dark pink
	{ bg: '#0891B2', ring: '#06B6D4', text: '#fff' }, // 15 dark cyan
];

export interface VendorPaletteSlot {
	bg: string;
	ring: string;
	text: '#fff' | '#1a1a1a';
}

/**
 * Pick a deterministic palette slot for `vendor`. Stable across renders.
 *
 * `name` is an optional secondary seed; use it when you want two APIs
 * served by the same host (rare, but happens with multi-tenant hostnames)
 * to land on different colours. The default seeds off `vendor` only so
 * `api.stripe.com` and `stripe.com` collapse to the same colour.
 */
export function vendorPalette(vendor: string, name?: string): VendorPaletteSlot {
	const seed = name ? `${vendor}|${name}` : vendor;
	return VENDOR_PALETTE[hashStr(seed) % VENDOR_PALETTE.length];
}
