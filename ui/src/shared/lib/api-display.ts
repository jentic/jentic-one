/**
 * API-identity display helpers — one shared humanising rule applied everywhere
 * a machine identity needs to render as a primary line for a human.
 *
 * The preferred input is the **catalog identity slug** (`catalog_api_id`,
 * e.g. `nytimes.com/article_search`) — persisted at import time and exposed on
 * API, credential, and toolkit-binding DTOs (#910) — because it is the only
 * identity form where the vendor and sub-API stay separable. Surfaces that
 * predate the column (or manually-imported APIs) fall back to humanising the
 * slugified `vendor`/`name` tuple.
 */

/**
 * Domain suffixes we preserve with a `.` join instead of expanding to a space,
 * so a real dotted domain like `posthog.com` renders as `Posthog.Com` rather
 * than `Posthog Com`. Kept intentionally short — grow the allowlist as we spot
 * new cases in the wild.
 */
const TLD_SUFFIXES = new Set([
	'com',
	'org',
	'net',
	'io',
	'dev',
	'ai',
	'app',
	'co',
	'xyz',
	// Common ccTLDs so a real dotted domain — including a multi-part public
	// suffix like `bbc.co.uk` — is recognised as a domain and keeps its dotted
	// structure instead of collapsing to spaces.
	'uk',
	'us',
	'eu',
	'de',
	'fr',
	'nl',
	'ca',
]);

/**
 * Shared core of the two humanisers below. Title-case a slug-ish segment for
 * display (`article_search` → `Article Search`), with the TLD dot-join rule
 * gated by `domainSlug`.
 *
 * Genuinely-dotted input (a real domain like `posthog.com`, `bbc.co.uk`, or a
 * multi-part `foo.bar.com`) preserves its dotted STRUCTURE: each dot-separated
 * label is title-cased on its own (internal hyphen/underscore/space runs
 * humanise to spaces) and the labels re-join with dots. This keeps a
 * multi-part public suffix readable (`bbc.co.uk` → `Bbc.Co.Uk`,
 * `foo.bar.com` → `Foo.Bar.Com`) instead of collapsing the leading dots into
 * spaces and only re-joining the last one (which produced `Foo Bar.Com`).
 *
 * TLD dot-join rule (the hyphenated path). Because the real vendor data in this
 * app is *hyphenated* (`posthog-com`, `github-com`), not dotted, a strict
 * "only-if-a-literal-dot" gate would wrongly space-join the real vendor slugs.
 * So a trailing suffix (see `TLD_SUFFIXES`) dot-joins when `domainSlug` is on
 * AND there are exactly 2 tokens — the shape of a real `<vendor>-<tld>` domain
 * slug (`posthog-com` → `Posthog.Com`). The "exactly 2 tokens" allowance means
 * 3+-token hyphenated product names like `stable-diffusion-ai` always stay
 * space-joined (`Stable Diffusion Ai`, killing the false positive).
 */
function humanize(segment: string, domainSlug: boolean): string {
	// A segment that already carried a real dot is a genuinely dotted domain.
	// When its trailing label is a recognised TLD (`TLD_SUFFIXES`), preserve the
	// dotted STRUCTURE so a multi-part public suffix (`bbc.co.uk`,
	// `foo.bar.com`) stays dot-joined across ALL labels rather than only the
	// trailing TLD — the old rule produced `Foo Bar.Com` by collapsing the
	// leading dots into spaces. Each dot label is title-cased independently (its
	// own hyphen/underscore/space runs humanise to spaces).
	if (segment.includes('.')) {
		const labels = segment.split('.').filter(Boolean);
		const lastLabel = labels[labels.length - 1] ?? '';
		if (TLD_SUFFIXES.has(lastLabel.toLowerCase())) {
			return labels.map((label) => titleCaseWords(label)).join('.');
		}
		// A dotted input whose trailing label ISN'T a known TLD (`acme.biz`)
		// isn't treated as a domain — space-join every token as before.
		return titleCaseTokens(segment).join(' ');
	}
	const parts = titleCaseTokens(segment);
	if (parts.length < 2) return parts.join(' ');
	const last = parts[parts.length - 1];
	// Dot-join the trailing TLD on the domain-slug path for a 2-token
	// `<vendor>-<tld>` slug. 3+ tokens are a product name, so they space-join.
	const dotJoin = domainSlug && parts.length === 2;
	if (dotJoin && TLD_SUFFIXES.has(last.toLowerCase())) {
		return parts.slice(0, -1).join(' ') + '.' + last;
	}
	return parts.join(' ');
}

/** Title-case each `[_\-.\s]`-separated token, dropping empties. */
function titleCaseTokens(segment: string): string[] {
	return segment
		.split(/[_\-.\s]+/)
		.filter(Boolean)
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1));
}

/** Title-case one domain label's words (hyphen/underscore/space runs) into a
 * single space-joined string, e.g. `article-search` → `Article Search`. */
function titleCaseWords(label: string): string {
	return label
		.split(/[_\-\s]+/)
		.filter(Boolean)
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1))
		.join(' ');
}

/**
 * Humanise a **domain slug** — a `<vendor>` / `<vendor>-<tld>` identity where a
 * trailing allowlist TLD reads as a real domain. A 2-token hyphenated slug
 * dot-joins (`posthog-com` → `Posthog.Com`, `github-com` → `Github.Com`);
 * genuinely dotted input keeps its dot (`posthog.com` → `Posthog.Com`); 3+-token
 * product names stay space-joined (`stable-diffusion-ai` → `Stable Diffusion
 * Ai`). Use for the `vendor` field.
 */
export function humanizeDomainSlug(segment: string): string {
	return humanize(segment, true);
}

/**
 * Humanise a **product / sub-API name** conservatively — a trailing allowlist
 * TLD only rejoins with a dot when the raw segment carried a *real* dot, so a
 * hyphenated endpoint name like `bar-io` reads `Bar Io` (not `Bar.Io`) and
 * `posthog-com` reads `Posthog Com`. Use for sub-API `name` segments.
 */
export function humanizeName(segment: string): string {
	return humanize(segment, false);
}

/**
 * Derive a distinct, human-readable title from a catalog `api_id`.
 *
 * An umbrella vendor exposes several sub-APIs that all share one `vendor`
 * (e.g. `nytimes.com/article_search`, `nytimes.com/books` both vendor
 * `nytimes.com`). Titling cards by `vendor` makes those rows indistinguishable,
 * so the title is built from the part of the `api_id` that actually varies:
 *
 *   `nytimes.com/article_search` → `Article Search`  (sub-API segment)
 *   `stripe.com`                 → `stripe.com`       (no sub-API; bare-domain
 *                                                     fallback — the input is
 *                                                     returned unchanged)
 */
export function titleFromApiId(apiId: string): string {
	const slash = apiId.indexOf('/');
	if (slash === -1) {
		return apiId;
	}
	const sub = apiId.slice(slash + 1);
	// A sub-API segment is an endpoint/product name, not a domain slug, so it
	// uses the conservative name humaniser: `bar-io` → `Bar Io`, not `Bar.Io`.
	// Only a genuinely-dotted sub-segment keeps its dot.
	return sub ? humanizeName(sub) : apiId;
}

/**
 * Names that carry no distinguishing information — a placeholder rather than a
 * real sub-API. When `api_name` is one of these, prefer the vendor as the
 * primary line rather than rendering "Main" / "Default" as a title.
 */
const GENERIC_NAMES = new Set(['', 'main', 'default']);

/** Separator run between a mirrored vendor prefix and the sub-API tail. */
const PREFIX_SEPARATORS = /^[-_.:/\s|]+/;

/**
 * Peel a repeated vendor prefix off a sub-API `name`. Legacy rows (no
 * `catalog_api_id`) often mirror the vendor into `name` — e.g.
 * `vendor='nytimes-com'`, `name='nytimes-com-article-search'` — so a naive
 * humanisation renders `Nytimes Com Article Search`. Stripping the exact
 * vendor prefix (plus a separator) yields `Article Search`, matching what
 * `titleFromApiId` derives when the slug is available.
 *
 * Deliberately minimal: only an EXACT, separator-delimited vendor prefix
 * strips. Anything cleverer (e.g. peeling a mirrored-TLD token) belongs to the
 * persisted slug now, not to guesswork here.
 */
function stripVendorPrefix(name: string, vendor: string): string {
	if (!name.toLowerCase().startsWith(vendor.toLowerCase())) return name;
	const rest = name.slice(vendor.length);
	// The name is EXACTLY the vendor — no distinguishing sub-API segment;
	// return empty so the caller falls back to the single vendor humanisation.
	if (rest === '') return '';
	// Require a separator after the prefix so `posthog` doesn't strip out of
	// `posthograph`.
	if (!PREFIX_SEPARATORS.test(rest)) return name;
	return rest.replace(PREFIX_SEPARATORS, '');
}

/** Shared legacy fallback: friendly name from the slugified vendor/name tuple. */
function tupleDisplayName(vendor: string, rawName: string): string {
	// `name` on real data can itself be a `vendor/name`-shaped tuple
	// (`posthog-com/posthog-com-posthog-api`) — take the tail so the prefix
	// strip sees just the sub-API segment.
	const name = rawName.includes('/') ? (rawName.split('/').pop() ?? rawName) : rawName;
	const stripped = vendor && name ? stripVendorPrefix(name, vendor) : name;
	if (stripped && !GENERIC_NAMES.has(stripped.toLowerCase())) {
		return humanizeName(stripped);
	}
	if (vendor) return humanizeDomainSlug(vendor);
	// Only humanise a bare `name` when it carries real information — a generic
	// placeholder (`main`, `default`) must not surface as `Main` / `Default`.
	if (name && !GENERIC_NAMES.has(name.toLowerCase())) return humanizeName(name);
	return '';
}

/**
 * API display name for the credential picker rows, the workspace `ApiCard` /
 * detail heading, and the credential card's friendly fallback.
 *
 *   1. Explicit `displayName` wins verbatim (user-set label) — but only when
 *      it carries non-whitespace content; a whitespace-only string is treated
 *      as absent so it can't render as a blank primary line.
 *   2. `catalogApiId` (the persisted catalog slug) titles exactly like
 *      Discover: sub-API segment humanised (`nytimes.com/article_search` →
 *      `Article Search`), bare domain verbatim (`stripe.com`).
 *   3. Legacy fallback — rows predating the persisted slug — humanises the
 *      slugified `vendor`/`name` tuple, minus a repeated vendor prefix.
 *   4. Empty when nothing usable is present.
 */
export function apiRefDisplayName(input: {
	displayName?: string | null;
	catalogApiId?: string | null;
	vendor?: string | null;
	name?: string | null;
}): string {
	// A user-set label wins verbatim, but only when it carries non-whitespace
	// content. Return the *trimmed* value so padded labels can't leak into
	// headings (or the seeded credential name).
	const dn = input.displayName?.trim();
	if (dn) return dn;
	const apiId = input.catalogApiId?.trim();
	if (apiId) return titleFromApiId(apiId);
	return tupleDisplayName(input.vendor ?? '', input.name ?? '');
}

/**
 * Toolkit-binding-row display name — same rule as {@link apiRefDisplayName}
 * keyed to the binding DTO's snake_case identity fields
 * (`ToolkitCredentialBindingResponse`).
 */
export function toolkitCredDisplayName(input: {
	catalog_api_id?: string | null;
	api_vendor?: string | null;
	api_name?: string | null;
}): string {
	const apiId = input.catalog_api_id?.trim();
	if (apiId) return titleFromApiId(apiId);
	return tupleDisplayName(input.api_vendor ?? '', input.api_name ?? '');
}

/**
 * Raw machine-identity subtitle. The persisted catalog slug wins verbatim —
 * it IS the machine identity (`nytimes.com/article_search`) and is what the
 * user saw when they picked the API. Legacy rows join `vendor/name`, dropping
 * a leading vendor repeat when `name` is itself a `vendor/name`-shaped tuple
 * (so it can't render `posthog-com/posthog-com/…`). Returns whichever single
 * field exists when the others are absent, or `''` when all are.
 */
export function apiIdentityTuple(input: {
	catalogApiId?: string | null;
	vendor?: string | null;
	name?: string | null;
}): string {
	const apiId = input.catalogApiId?.trim();
	if (apiId) return apiId;
	const vendor = input.vendor ?? '';
	const rawName = input.name ?? '';
	let name = rawName;
	if (vendor && rawName.toLowerCase().startsWith(`${vendor.toLowerCase()}/`)) {
		name = rawName.slice(vendor.length + 1);
	}
	if (vendor && name) return `${vendor}/${name}`;
	return vendor || name;
}
