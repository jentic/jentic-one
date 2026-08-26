/**
 * Expressive rendering of a delivery's `last_error`.
 *
 * The backend deliberately stores only a **stable, non-sensitive** category in
 * `last_error` (see `WebhookDeliveryDispatcher._categorize_error`): a resolved
 * internal IP, a raw exception repr, or an upstream body must never reach the
 * API or this table. So the raw value is safe to show — but on its own it is
 * opaque ("blocked_egress_policy" tells an operator nothing actionable).
 *
 * This module turns each known category into a short human **label** plus a
 * one-line **explanation** (and, where useful, a remediation hint) for the
 * tooltip. The status-carrying categories (`http_error_500`) are parsed for
 * their numeric code — which is itself non-sensitive — so the label can read
 * "HTTP 500". Anything unrecognised (an older row, a future category) falls
 * back to showing the stored value verbatim, which is safe by construction.
 */

/** The presentational shape the Error cell renders. */
export interface DeliveryErrorDisplay {
	/** Short label for the cell, e.g. "Blocked by IP allowlist" or "HTTP 500". */
	label: string;
	/** One-line explanation + remediation hint for the tooltip / aria-label. */
	description: string;
}

/**
 * The closed set of non-HTTP categories the dispatcher can persist, mapped to
 * operator-facing copy. Keep this in lockstep with the backend enum in
 * `_categorize_error`; an unknown value falls through to the raw-string case.
 */
const REASON_COPY: Record<string, DeliveryErrorDisplay> = {
	blocked_by_allowlist: {
		label: 'Blocked by IP allowlist',
		description:
			"The destination's IP is not within this endpoint's allowed CIDR ranges. Edit the allowlist under Settings → Advanced, or clear it to allow any destination.",
	},
	blocked_egress_policy: {
		label: 'Blocked by egress policy',
		description:
			'The destination resolved to a private, loopback, or cloud-metadata address that the server-wide SSRF policy refuses. Point the endpoint at a public URL.',
	},
	dns_unresolved: {
		label: 'Host did not resolve',
		description:
			"The target hostname could not be resolved to an address. Check the endpoint's URL, or that its tunnel/DNS record is live.",
	},
	connection_timeout: {
		label: 'Connection timed out',
		description:
			'The destination did not accept a connection in time. It may be down, overloaded, or blocking us at the network layer.',
	},
	read_timeout: {
		label: 'Response timed out',
		description:
			'The destination accepted the connection but did not respond in time. Check that the receiver returns promptly.',
	},
	connect_failed: {
		label: 'Connection failed',
		description:
			'The connection to the destination was refused or the host was unreachable. Confirm the target is listening and reachable.',
	},
	tls_error: {
		label: 'TLS handshake failed',
		description:
			"The TLS/SSL handshake with the destination failed — often an expired, self-signed, or mismatched certificate. Check the receiver's certificate.",
	},
	protocol_error: {
		label: 'Protocol error',
		description:
			'The destination spoke an unexpected or malformed HTTP protocol. Check that the target is a real HTTP(S) endpoint.',
	},
	transport_error: {
		label: 'Network error',
		description:
			'A network-transport error prevented the delivery from reaching the destination.',
	},
	response_too_large: {
		label: 'Response too large',
		description:
			'The receiver returned a response body larger than the allowed cap. Only the status code is needed — return a small (or empty) body.',
	},
	endpoint_gone_deactivated: {
		label: 'Endpoint deactivated (410 Gone)',
		description:
			'The receiver answered 410 Gone, asking us to stop. The endpoint has been paused; re-activate it under Settings once the receiver is ready.',
	},
	delivery_error: {
		label: 'Delivery failed',
		description: 'The delivery failed for an unrecognised reason. See server logs for detail.',
	},
};

/** Matches the status-carrying category `http_error_<code>` (e.g. `http_error_503`). */
const HTTP_ERROR_RE = /^http_error_(\d{3})$/;

/**
 * Map a stored `last_error` category to its label + explanation.
 *
 * Returns `null` for a null/empty error (the caller renders a dash). An HTTP
 * status category becomes "HTTP <code>" with a 4xx/5xx-aware hint; an
 * unrecognised value is shown verbatim (safe: the backend never stores
 * sensitive text there).
 */
export function describeDeliveryError(lastError: string | null): DeliveryErrorDisplay | null {
	if (!lastError) return null;

	const known = REASON_COPY[lastError];
	if (known) return known;

	const httpMatch = HTTP_ERROR_RE.exec(lastError);
	if (httpMatch) {
		const code = Number(httpMatch[1]);
		const family = code >= 500 ? 'server' : 'client';
		return {
			label: `HTTP ${code}`,
			description:
				family === 'server'
					? `The receiver returned ${code}, a server error. It will be retried; check the receiver's logs.`
					: `The receiver returned ${code}, a client error. Check the URL, auth, and payload the receiver expects.`,
		};
	}

	// Unknown category (e.g. a legacy row). Safe to show as-is — the backend
	// guarantees this field carries no sensitive detail.
	return { label: lastError, description: lastError };
}
