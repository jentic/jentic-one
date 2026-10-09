/**
 * What a catalog row can say about an entry from the `GET /catalog` LIST
 * payload alone (`CatalogEntryResponse`: api_id, vendor, path, spec_url, …) —
 * no per-row requests.
 *
 * The one extra fact the list carries is the spec version, and only
 * implicitly: every entry the backend builds (`manifest_builder.parse_apis_json`)
 * points `spec_url` at the jentic-public-apis layout
 *
 *   https://raw.githubusercontent.com/jentic/jentic-public-apis/{ref}/apis/openapi/{domain}/{sub}/{version}/openapi.json
 *
 * where `{ref}` is a branch, bare (`main`) or fully qualified
 * (`refs/heads/main`, which the live manifest serves),
 *
 * and `{sub}` is the umbrella sub-API (`nytimes.com/books/…`) or a
 * version/branch marker when there is none (`stripe.com/main/2024-01-01/…`).
 * We read `{version}` ONLY when the URL matches that layout exactly AND its
 * `{domain}`/`{sub}` agree with the entry's own `api_id` — anything else (a
 * custom manifest, a different host, a directory that isn't version-like)
 * yields nothing rather than a guess.
 */

const PUBLIC_APIS_SPEC_URL_RE =
	/^https:\/\/raw\.githubusercontent\.com\/jentic\/jentic-public-apis\/(?:refs\/heads\/)?[^/]+\/apis\/openapi\/([^/]+)\/([^/]+)\/([^/]+)\/openapi\.(json|ya?ml)$/;

/**
 * A `{sub}` that's a version/branch marker (so the api_id is the bare domain).
 * Mirrors the backend's `_VERSION_SUBDIR_RE`.
 */
const VERSION_SUBDIR_RE = /^(main|master|latest|heads|v\d|[0-9])/i;

/** A version directory must carry a digit (`2024-01-01`, `1.0.0`, `v3`) — never `main`. */
const VERSION_LIKE_RE = /\d/;

/** The spec version directory (e.g. `2024-01-01`), or null when it can't be read safely. */
export function parseCatalogSpecUrl(
	specUrl: string | null | undefined,
	apiId: string,
): string | null {
	if (!specUrl) return null;
	const match = PUBLIC_APIS_SPEC_URL_RE.exec(specUrl);
	if (!match) return null;
	const [, domain, sub, rawVersion] = match;
	let version: string;
	try {
		version = decodeURIComponent(rawVersion);
	} catch {
		return null;
	}
	// The URL must be this entry's own: same api_id the backend would derive.
	const derivedApiId = VERSION_SUBDIR_RE.test(sub) ? domain : `${domain}/${sub}`;
	if (derivedApiId !== apiId) return null;
	if (!VERSION_LIKE_RE.test(version)) return null;
	return version;
}

/** Display form of a version: `v2024-01-01`, `v1.0.0` — never `vv3`. */
export function versionLabel(version: string): string {
	return /^v/i.test(version) ? version : `v${version}`;
}
