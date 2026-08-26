/**
 * Target-URL validation helpers shared by the create sheet and the inline
 * Settings-tab configuration editor.
 *
 * Both entry points do the same two things with a target URL: a cheap
 * client-side structural check before a round-trip, and pinning the server's
 * rejection reason to the field when the backend refuses a disallowed URL.
 * Keeping them here means the create and edit surfaces stay in lock-step
 * (there is one notion of "what a valid target URL looks like") and neither has
 * to reach into the other's component.
 */
import { WebhooksApiError } from '@/modules/webhooks/api';

/**
 * A cheap, client-side sanity check for the target URL: it must parse as an
 * absolute `http`/`https` URL. This is *not* the security boundary — the egress
 * guard re-validates (and re-resolves) at send time, and the server rejects
 * disallowed URLs at create/update with a precise reason surfaced separately.
 * This only catches the obvious typo before a round-trip. Returns an error
 * string, or `null` when the value looks structurally fine.
 */
export function validateTargetUrl(raw: string): string | null {
	const value = raw.trim();
	if (!value) return 'A notification endpoint needs a target URL to POST to.';
	let parsed: URL;
	try {
		parsed = new URL(value);
	} catch {
		return 'Enter a full URL, including https://.';
	}
	if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
		return 'The URL must use http:// or https://.';
	}
	if (!parsed.hostname) return 'The URL is missing a host.';
	return null;
}

/**
 * The server rejects a disallowed target URL with a 400 whose message begins
 * with `target_url` (Phase 0's `InvalidInputError`, e.g.
 * `"target_url is not allowed: …"`). When that happens we want to pin the
 * message *to the field* rather than let it vanish into a generic toast — so the
 * user sees exactly why the address they typed was refused, right where they
 * typed it.
 */
export function targetUrlServerError(error: unknown): string | null {
	if (error instanceof WebhooksApiError && error.status === 400) {
		const msg = error.message.trim();
		if (/target_url/i.test(msg)) return msg;
	}
	return null;
}
