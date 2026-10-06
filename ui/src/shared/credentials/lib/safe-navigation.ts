// Safe-navigation helpers for URLs sourced from vendor OAuth responses.
//
// The connect flow relays several URLs from the vendor into the user's browser
// — `verification_uri` / `verification_uri_complete` (device flow) and
// `authorize_url` (auth-code). All of them arrive as free-form strings in the
// vendor's JSON response, so a compromised or misconfigured vendor could hand
// back `javascript:...` or `data:...` and turn a `window.open` call into an
// XSS on our origin or a data-URI phishing page.
//
// Every call site that would otherwise pass a vendor-supplied URL directly to
// `window.open` / `window.location.assign` funnels through the helpers here.
// The rule is simple: HTTPS only. HTTP is refused too — real OAuth vendors
// never redirect over plaintext, and treating http as unsafe closes a
// downgrade-attack window with zero legitimate cost.

/**
 * Raised when a vendor-supplied URL isn't safe to navigate to.
 *
 * The `reason` is human-readable and safe to surface in the UI; the raw URL
 * is deliberately NOT embedded in the message (a malicious value could
 * contain user-facing text designed to social-engineer the operator).
 */
export class UnsafeVendorUrlError extends Error {
	constructor(public readonly reason: string) {
		super(`Vendor returned an unsafe URL (${reason}). Refusing to open.`);
		this.name = 'UnsafeVendorUrlError';
	}
}

/**
 * Return `true` iff `raw` parses as a well-formed `https:` URL. Anything else
 * — `javascript:`, `data:`, `file:`, plain `http:`, an unparsable string, or
 * `null`/`undefined` — is unsafe.
 */
export function isHttpsVendorUrl(raw: string | null | undefined): boolean {
	if (!raw) return false;
	try {
		return new URL(raw).protocol === 'https:';
	} catch {
		return false;
	}
}

/**
 * Parse `raw` and require an `https:` scheme; throw `UnsafeVendorUrlError`
 * otherwise. Returns the parsed URL for callers that need the normalised
 * form.
 */
export function assertHttpsVendorUrl(raw: string | null | undefined): URL {
	if (!raw) throw new UnsafeVendorUrlError('empty URL');
	let parsed: URL;
	try {
		parsed = new URL(raw);
	} catch {
		throw new UnsafeVendorUrlError('unparsable URL');
	}
	if (parsed.protocol !== 'https:') {
		throw new UnsafeVendorUrlError(`unsafe scheme "${parsed.protocol}"`);
	}
	return parsed;
}

/**
 * Wrapper around `window.open` that first validates `raw` is an `https:` URL.
 * Throws `UnsafeVendorUrlError` if it isn't; otherwise delegates to
 * `window.open(...)` and returns its result (which may still be `null` if the
 * browser blocked the popup — that's a legitimate browser-side outcome, not
 * an unsafe-URL failure).
 */
export function openVendorUrl(
	raw: string | null | undefined,
	target?: string,
	features?: string,
): Window | null {
	assertHttpsVendorUrl(raw);
	// Cast is safe: assertHttps returned without throwing, so `raw` is a
	// non-empty https URL string.
	return window.open(raw as string, target, features);
}

/**
 * Wrapper around `window.location.assign` that first validates `raw` is an
 * `https:` URL. Throws `UnsafeVendorUrlError` if it isn't; otherwise delegates
 * to `window.location.assign(...)`.
 */
export function assignVendorUrl(raw: string | null | undefined): void {
	assertHttpsVendorUrl(raw);
	window.location.assign(raw as string);
}
