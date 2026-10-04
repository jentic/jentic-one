/**
 * Catalog figures the backend can't provide yet — STATIC PLACEHOLDERS.
 *
 * TODO(catalog-vendor-stats): every value in this module is placeholder data,
 * pending a backend source (a vendor total on `GET /catalog`, per-vendor API
 * counts, and per-letter vendor counts / a letter seek). Nothing else in the
 * Library may hard-code these; swap this module for real fields when they
 * land and delete it. Everything else on the page reads real data.
 */

/** "… from N vendors" in the Library count line. Placeholder. */
export const PLACEHOLDER_VENDOR_TOTAL = 4870;

/**
 * Total APIs per vendor domain in the whole catalog (not just loaded pages).
 * Used to label a collapsed vendor row ("+N more") before all of its APIs are
 * loaded. Unknown vendors fall back to loaded-so-far (`N+` while paging).
 * Placeholder.
 */
export const PLACEHOLDER_VENDOR_API_COUNTS: Readonly<Record<string, number>> = {
	'googleapis.com': 9,
	'nytimes.com': 3,
	'1password.com': 2,
	'amazonaws.com': 12,
	'azure.com': 14,
	'microsoft.com': 6,
	'twilio.com': 8,
};

/**
 * Vendors per A–Z rail letter across the whole catalog — feeds the rail's
 * tooltips and lets a letter that isn't loaded yet still be offered (the
 * ledger pages forward until it arrives). `#` is 0–9. Placeholder.
 */
export const PLACEHOLDER_LETTER_VENDOR_COUNTS: Readonly<Record<string, number>> = {
	'#': 96,
	A: 312,
	B: 241,
	C: 388,
	D: 214,
	E: 163,
	F: 172,
	G: 201,
	H: 118,
	I: 157,
	J: 41,
	K: 52,
	L: 149,
	M: 268,
	N: 124,
	O: 133,
	P: 287,
	Q: 19,
	R: 166,
	S: 402,
	T: 231,
	U: 64,
	V: 88,
	W: 102,
	X: 12,
	Y: 27,
	Z: 33,
};
