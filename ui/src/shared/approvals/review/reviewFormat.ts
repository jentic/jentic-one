/**
 * Plain-language formatting for the review pop-up and the bulk-approve
 * confirmation: the held request split into its URL and query parameters, the
 * body pretty-printed, the hold's window, and the amount a request body
 * carries. Values stay exactly as the agent sent them; only the labels are made
 * readable. Relative times ("5m ago", "expires in 23 h") are `heldCallCopy`'s,
 * so every surface words them the same way.
 */

/** Pretty-print a JSON body; anything else is shown verbatim. */
export function formatBody(body: string): string {
	try {
		return JSON.stringify(JSON.parse(body), null, 2);
	} catch {
		return body;
	}
}

/** The hold's window in whole hours ("24"), from when it was held to when it expires. */
export function holdHours(createdAt: string, expiresAt: string): number | null {
	const hours = Math.round((Date.parse(expiresAt) - Date.parse(createdAt)) / 3_600_000);
	return Number.isFinite(hours) && hours > 0 ? hours : null;
}

/** Less than an hour left: the expiry is worth an accent. */
export function expiresSoon(iso: string, now: number = Date.now()): boolean {
	return Date.parse(iso) - now < 3_600_000;
}

/** A held request's URL without its query, and the query's parameters in order. */
export function splitUrl(url: string): { base: string; params: [string, string][] } {
	try {
		const parsed = new URL(url);
		// Only a web address splits into origin, path and query; anything else
		// is shown as it was sent.
		if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:')
			return { base: url, params: [] };
		const params = [...parsed.searchParams.entries()];
		return { base: `${parsed.origin}${parsed.pathname}`, params };
	} catch {
		const [base, query = ''] = url.split('?');
		return { base, params: [...new URLSearchParams(query).entries()] };
	}
}

/** "invoice_now" → "Invoice now", "customerId" → "Customer id": a readable label for a raw key. */
export function humanKey(key: string): string {
	const words = key
		.replace(/([a-z0-9])([A-Z])/g, '$1 $2')
		.replace(/[_\-.[\]]+/g, ' ')
		.trim()
		.toLowerCase();
	return words ? words[0].toUpperCase() + words.slice(1) : key;
}

/** The amount a JSON body carries at its top level, as sent, with its currency when it has one. */
export interface BodyAmount {
	amount: number;
	currency: string | null;
}

export function bodyAmount(body: string | null | undefined): BodyAmount | null {
	if (!body) return null;
	let parsed: unknown;
	try {
		parsed = JSON.parse(body);
	} catch {
		return null;
	}
	if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
	const { amount, currency } = parsed as { amount?: unknown; currency?: unknown };
	if (typeof amount !== 'number' || !Number.isFinite(amount)) return null;
	return {
		amount,
		currency: typeof currency === 'string' && currency.trim() ? currency.trim() : null,
	};
}

/**
 * The amounts summed, only when every amount carries the same currency: a sum
 * across currencies, or over an amount without one, means nothing.
 */
export function totalAmount(
	amounts: readonly (BodyAmount | null)[],
): { amount: number; currency: string; count: number } | null {
	const present = amounts.filter((a): a is BodyAmount => a !== null);
	if (present.length === 0) return null;
	const currencies = new Set(present.map((a) => a.currency?.toLowerCase() ?? null));
	if (currencies.size !== 1 || currencies.has(null)) return null;
	return {
		amount: present.reduce((sum, a) => sum + a.amount, 0),
		currency: present[0].currency!,
		count: present.length,
	};
}
