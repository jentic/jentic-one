/**
 * API-identity display helpers — one shared humanising rule applied everywhere
 * a machine identity (`api_id`, `api_vendor`, `api_name`) needs to render as a
 * primary line for a human.
 *
 * The rule (`humanizeSegment`) is originally from the Discover module's
 * catalog adapter; it lives here so every surface — Discover, the
 * credential picker's catalog rows, the toolkit "Bound Credentials" row, the
 * toolkit "Bind API" picker — reads the same "friendly name" from whichever
 * identity field its DTO happens to carry.
 *
 * See `docs/plans/issue-631-friendly-api-names.md` for the decision log and
 * worked examples across surfaces.
 */

/**
 * Domain suffixes we preserve with a `.` join instead of expanding to a space.
 * Keeps `posthog-com` rendering as `Posthog.Com` rather than `Posthog Com`.
 * Kept intentionally short — grow the allowlist as we spot new cases in the
 * wild.
 */
const TLD_SUFFIXES = new Set(['com', 'org', 'net', 'io', 'dev', 'ai', 'app', 'co', 'xyz']);

/**
 * Title-case a slug-ish segment for display: `article_search` → `Article Search`,
 * `top-stories` → `Top Stories`, `v2` → `V2`. When the last token is a common
 * domain suffix (see `TLD_SUFFIXES`), rejoin it with a dot instead of a space:
 * `posthog-com` → `Posthog.Com`, `foo-bar-com` → `Foo Bar.Com`.
 */
export function humanizeSegment(segment: string): string {
	const parts = segment
		.split(/[_\-.]+/)
		.filter(Boolean)
		.map((word) => word.charAt(0).toUpperCase() + word.slice(1));
	if (parts.length < 2) return parts.join(' ');
	const last = parts[parts.length - 1];
	if (TLD_SUFFIXES.has(last.toLowerCase())) {
		return parts.slice(0, -1).join(' ') + '.' + last;
	}
	return parts.join(' ');
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
	return sub ? humanizeSegment(sub) : apiId;
}

/**
 * Friendly display for a bare `api_vendor` slug (no `/`), e.g. `posthog-com` →
 * `Posthog.Com`. Alias of `humanizeSegment` — a separate name so call sites
 * read honestly about what shape of input they're passing.
 */
export const humanizeVendor = (vendor: string): string => humanizeSegment(vendor);

/**
 * Names that carry no distinguishing information — a placeholder rather than a
 * real sub-API. When `api_name` is one of these, prefer the vendor as the
 * primary line rather than rendering "Main" / "Default" as a title.
 */
const GENERIC_NAMES = new Set(['', 'main', 'default']);

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
	// Require a separator after the prefix so `posthog` doesn't strip out of
	// `posthograph`. An immediately-following alphanumeric means the vendor
	// slug isn't actually a prefix, so leave the name alone.
	if (rest.length === 0) return name;
	if (/^[-_./]/.test(rest)) return rest.replace(/^[-_./]+/, '');
	return name;
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
		return humanizeSegment(stripped);
	}
	if (vendor) return humanizeVendor(vendor);
	// Only humanise a bare `api_name` when it isn't a generic placeholder,
	// so a vendor-less `main` / `default` binding doesn't render as `Main`.
	if (rawName && !GENERIC_NAMES.has(tailAfterSlash.toLowerCase()))
		return humanizeSegment(tailAfterSlash);
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
 *   1. Explicit `displayName` wins verbatim (user-set label).
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
	if (input.displayName) return input.displayName;
	const vendor = input.vendor ?? '';
	const name = input.name ?? '';
	const stripped = vendor && name ? stripVendorPrefix(name, vendor) : name;
	if (stripped && !GENERIC_NAMES.has(stripped.toLowerCase())) {
		return humanizeSegment(stripped);
	}
	if (vendor) return humanizeVendor(vendor);
	// Only humanise a bare `name` when it carries real information — a generic
	// placeholder (`main`, `default`) must not surface as `Main` / `Default`.
	if (name && !GENERIC_NAMES.has(name.toLowerCase())) return humanizeSegment(name);
	return '';
}
