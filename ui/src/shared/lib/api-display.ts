/**
 * API-identity display helpers — one shared humanising rule applied everywhere
 * a machine identity (`api_id`, `api_vendor`, `api_name`) needs to render as a
 * primary line for a human.
 *
 * The rule (`humanizeDomainSlug` / `humanizeName`) is originally from the
 * Discover module's catalog adapter; it lives here so every surface — Discover,
 * credential picker's catalog rows, the toolkit "Bound Credentials" row, the
 * toolkit "Bind API" picker — reads the same "friendly name" from whichever
 * identity field its DTO happens to carry.
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
 * Peel a repeated vendor prefix off a sub-API `name`. Real payloads often
 * mirror the vendor into `name` — e.g. `vendor='posthog-com'`,
 * `name='posthog-com-posthog-api'` — so a naive humanisation renders
 * `Posthog Com Posthog Api`. Stripping the vendor prefix (with hyphen/dot/
 * underscore/slash separator, case-insensitive) yields `Posthog Api`, which
 * matches how Discover's `titleFromApiId` extracts the sub-API segment.
 * Returns the input unchanged when the prefix doesn't match.
 */
function stripVendorPrefix(name: string, vendor: string): string {
	const nameLower = name.toLowerCase();
	const vendorLower = vendor.toLowerCase();
	if (!nameLower.startsWith(vendorLower)) return name;
	const rest = name.slice(vendor.length);
	// The name is EXACTLY the vendor (`name === vendor`) — there's no
	// distinguishing sub-API segment, so return empty and let the caller fall
	// through to the single vendor humanisation. Otherwise the two paths would
	// render the same identity two different ways (`humanizeName('github-com')`
	// → `Github Com` vs `humanizeDomainSlug('github-com')` → `Github.Com`).
	if (rest === '') return '';
	// Require a separator after the prefix so `posthog` doesn't strip out of
	// `posthograph`. An immediately-following alphanumeric means the vendor
	// slug isn't actually a prefix, so leave the name alone. Real payloads
	// separate the prefix with a hyphen/dot/underscore/slash, but also
	// sometimes a space / colon / pipe (`posthog com posthog-api`), so treat
	// all of those as separators too.
	if (!PREFIX_SEPARATORS.test(rest)) return name;
	const tail = rest.replace(PREFIX_SEPARATORS, '');
	// The mirrored prefix is often the vendor's DOMAIN form, not the bare
	// vendor slug: `vendor='posthog'`, `name='posthog com posthog-api'` mirrors
	// `posthog.com`. Stripping only the vendor slug would orphan the TLD token
	// (`Com Posthog Api` — the exact double-render the strip exists to
	// prevent, shifted one token right), so when the tail's LEADING token is a
	// recognised TLD, peel it as part of the vendor prefix too. A tail that
	// was ONLY the TLD (`vendor='posthog'`, `name='posthog-com'`) is a fully
	// consumed mirror — return empty so the caller falls back to the vendor.
	//
	// The peel is gated on the vendor NOT already carrying its own TLD: for
	// `vendor='apple-com'`, `name='apple-com-app-store-connect'` the mirrored
	// domain is already fully consumed by the prefix strip, so a leading
	// TLD-shaped token (`app`, `ai`, `dev`, …) is a genuine part of the
	// sub-API name — peeling it would truncate `App Store Connect` to
	// `Store Connect`.
	const vendorTokens = vendorLower.split(/[-_.:/\s|]+/).filter(Boolean);
	const vendorHasTld = TLD_SUFFIXES.has(vendorTokens[vendorTokens.length - 1] ?? '');
	if (!vendorHasTld) {
		const tldMatch = /^([A-Za-z]+)(.*)$/.exec(tail);
		if (tldMatch && TLD_SUFFIXES.has(tldMatch[1].toLowerCase())) {
			const afterTld = tldMatch[2];
			if (afterTld === '') return '';
			if (PREFIX_SEPARATORS.test(afterTld)) return afterTld.replace(PREFIX_SEPARATORS, '');
		}
	}
	return tail;
}

/**
 * Toolkit-binding-row display name.
 *
 * The toolkit "Bound Credentials" DTO (`ToolkitCredentialBindingResponse`)
 * exposes `api_vendor` + `api_name` but **no** `api_id` — so we can't feed
 * `titleFromApiId` here. Instead we apply the same humanising rule to
 * whichever identity field is most informative:
 *   1. When `api_name` carries a real sub-API segment (not empty and not one
 *      of `GENERIC_NAMES`), humanise `stripVendorPrefix(api_name, api_vendor)`
 *      so `posthog-com/posthog-com-posthog-api` reads `Posthog Api` — the
 *      Discover-style output for the umbrella sub-API case.
 *   2. Otherwise humanise `api_vendor` (`posthog-com` → `Posthog.Com`).
 *   3. Empty when both are absent.
 */
export function toolkitCredDisplayName(input: {
	api_vendor?: string | null;
	api_name?: string | null;
}): string {
	const vendor = input.api_vendor ?? '';
	// `api_name` on real data can be a `vendor/name`-shaped tuple; peel that
	// leading slash off first so the prefix strip below sees just the tail.
	const rawName = input.api_name ?? '';
	const tailAfterSlash = rawName.includes('/') ? (rawName.split('/').pop() ?? rawName) : rawName;
	const stripped = vendor ? stripVendorPrefix(tailAfterSlash, vendor) : tailAfterSlash;
	if (stripped && !GENERIC_NAMES.has(stripped.toLowerCase())) {
		return humanizeName(stripped);
	}
	if (vendor) return humanizeDomainSlug(vendor);
	// Only humanise a bare `api_name` when it isn't a generic placeholder,
	// so a vendor-less `main` / `default` binding doesn't render as `Main`.
	if (rawName && !GENERIC_NAMES.has(tailAfterSlash.toLowerCase()))
		return humanizeName(tailAfterSlash);
	return '';
}

/**
 * Local-API display name for the credential picker's workspace rows, the
 * workspace `ApiCard`, and the credential card's friendly sub-line.
 *
 * Every consumer shape falls back to `vendor/name` when a `display_name`
 * isn't set — which for a freshly-imported API is the common case, and
 * reads as the raw `posthog-com/posthog-com-posthog-api` tuple. So we route
 * that fallback through the same rule the toolkit surface uses:
 *   1. Explicit `displayName` wins verbatim (user-set label) — but only when
 *      it carries non-whitespace content; a whitespace-only string is treated
 *      as absent so it can't render as a blank primary line.
 *   2. `name` humanised, minus a repeated vendor prefix — so
 *      `vendor='nytimes-com', name='nytimes-com-article-search'` reads as
 *      `Article Search`, matching Discover. Generic names (`main`,
 *      `default`, ``) are skipped.
 *   3. Otherwise humanise `vendor` (`posthog-com` → `Posthog.Com`).
 *   4. Empty when nothing usable is present.
 */
export function apiRefDisplayName(input: {
	displayName?: string | null;
	vendor?: string | null;
	name?: string | null;
}): string {
	// A user-set label wins verbatim, but only when it carries non-whitespace
	// content. Return the *trimmed* value so padded labels can't leak into
	// headings (or the seeded credential name) — a whitespace-only string is
	// treated as absent and falls through.
	const dn = input.displayName?.trim();
	if (dn) return dn;
	const vendor = input.vendor ?? '';
	const name = input.name ?? '';
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
 * Raw machine-identity subtitle: `vendor/name`, both fields verbatim.
 *
 * `name` on real data can itself be a `vendor/name`-shaped tuple
 * (`posthog-com/posthog-com-posthog-api`) — the same shape
 * {@link toolkitCredDisplayName} peels for its title. Concatenating naively
 * would render the vendor twice (`posthog-com/posthog-com/…`), so when the
 * tuple's leading segment repeats the vendor it is dropped before joining.
 * Returns whichever single field exists when the other is absent, or `''`
 * when both are.
 */
export function apiIdentityTuple(input: { vendor?: string | null; name?: string | null }): string {
	const vendor = input.vendor ?? '';
	const rawName = input.name ?? '';
	let name = rawName;
	if (vendor && rawName.toLowerCase().startsWith(`${vendor.toLowerCase()}/`)) {
		name = rawName.slice(vendor.length + 1);
	}
	if (vendor && name) return `${vendor}/${name}`;
	return vendor || name;
}
