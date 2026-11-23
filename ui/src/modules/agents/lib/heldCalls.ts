/**
 * Pure helpers for the held calls the Agents page shows on an agent: which of
 * the agent's APIs a call goes to, and what one bulk deny tells the operator
 * once every decision is back.
 */
import type { HeldCall } from '@/shared/approvals';
import type { ApiTileModel } from '@/modules/agents/lib/apiTiles';

type ApiTitleSource = Pick<ApiTileModel, 'vendor' | 'apiName' | 'version' | 'title'>;

/**
 * The name of the agent's API a call goes to, as its API row shows it
 * ("Stripe"): the exact API first, then any version of it, then a vendor-wide
 * binding. A call to an API the agent has no row for reads as its vendor.
 */
export function heldCallApiTitle(
	call: Pick<HeldCall, 'api_vendor' | 'api_name' | 'api_version'>,
	tiles: readonly ApiTitleSource[],
): string {
	const sameVendor = tiles.filter((t) => t.vendor === call.api_vendor);
	const match =
		sameVendor.find((t) => t.apiName === call.api_name && t.version === call.api_version) ??
		sameVendor.find((t) => t.apiName === call.api_name) ??
		sameVendor.find((t) => t.apiName == null);
	return match?.title ?? call.api_vendor;
}

/** How a bulk deny went, one count per outcome. */
export interface DenyOutcome {
	denied: number;
	/** Someone else decided first (409): nothing left to deny. */
	alreadyDecided: number;
	/** Refused for any other reason; worth trying again. */
	failed: number;
}

const calls = (n: number) => `${n} ${n === 1 ? 'call' : 'calls'}`;

/**
 * The one toast a bulk deny ends with. All denied reads as a plain success;
 * calls someone else decided first are not a failure, just said; anything
 * else that failed asks for another try.
 */
export function denySummary(
	{ denied, alreadyDecided, failed }: DenyOutcome,
	agentName: string,
	/** What the first refusal said, when the server said anything. */
	firstError: string | null = null,
): { title: string; description?: string; variant: 'success' | 'info' | 'warning' | 'error' } {
	const total = denied + alreadyDecided + failed;
	const notes: string[] = [];
	if (alreadyDecided > 0) {
		notes.push(
			`${calls(alreadyDecided)} ${alreadyDecided === 1 ? 'was' : 'were'} already decided by someone else.`,
		);
	}
	if (failed > 0) {
		// The failed calls stay ticked on the card, so another try is one press.
		notes.push(
			`${calls(failed)} couldn’t be denied and ${failed === 1 ? 'is' : 'are'} still selected${firstError ? `: ${firstError}` : ''}. Try again.`,
		);
	}
	const description = notes.length > 0 ? notes.join(' ') : undefined;
	if (denied === total) {
		return { title: `Denied ${calls(denied)} from ${agentName}`, variant: 'success' };
	}
	if (denied === 0 && failed === 0) {
		return { title: 'Already decided', description, variant: 'info' };
	}
	if (denied === 0) {
		return { title: `Couldn’t deny ${calls(total)}`, description, variant: 'error' };
	}
	return {
		title: `Denied ${denied} of ${calls(total)}`,
		description,
		variant: failed > 0 ? 'warning' : 'info',
	};
}
