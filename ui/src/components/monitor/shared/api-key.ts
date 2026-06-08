/**
 * Helpers for building and parsing the composite "vendor:apiName" key used
 * as the value of the API filter dropdown. Kept in one place so all call
 * sites agree on the separator and decoding rules.
 */

const SEPARATOR = ':';

export function encodeApiKey(vendor: string, apiName: string): string {
	return `${vendor}${SEPARATOR}${apiName}`;
}

/**
 * Extract the vendor portion of a composite filter value. Uses `indexOf` +
 * `slice` rather than `split(':')[0]` so that vendor names which themselves
 * contain a colon (rare but possible) are not silently truncated. Only the
 * *first* colon is treated as the separator.
 */
export function decodeApiKeyVendor(key: string | null | undefined): string | undefined {
	if (!key) return undefined;
	const sep = key.indexOf(SEPARATOR);
	const vendor = sep === -1 ? key : key.slice(0, sep);
	return vendor || undefined;
}

export function decodeApiKeyName(key: string | null | undefined): string | undefined {
	if (!key) return undefined;
	const sep = key.indexOf(SEPARATOR);
	if (sep === -1) return undefined;
	const name = key.slice(sep + 1);
	return name || undefined;
}
