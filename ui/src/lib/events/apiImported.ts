/**
 * Cross-tab event channel for catalog API import lifecycle.
 *
 * Sibling of `credentialImported`. We split the two for v2 because the
 * single-channel approach was overloading the meaning of
 * "credentialImported" — adding a credential implicitly imports the
 * catalog API as a side effect, and Discover was reacting to *both*
 * lifecycle changes through one event. That conflated two concerns:
 *
 *   - "the workspace just gained an API"  → Discover should refresh
 *      the catalog row's "Available" → "In workspace" pill.
 *   - "the workspace just gained a credential" → CredentialsPage and
 *      OAuthCardSection should refresh.
 *
 * Splitting makes those subscribers strictly orthogonal, which matters
 * for the new flow where users can also import an API *without* adding
 * a credential (Discover sheet → "Add to workspace" CTA).
 *
 * The transport mirrors `credentialImported` exactly — BroadcastChannel
 * for cross-tab plus an in-page EventTarget so the emitting tab also
 * receives the event (BroadcastChannel skips the sender by spec).
 */

const CHANNEL_NAME = 'jentic.apis';
const EVENT_TYPE = 'apiImported';

export interface ApiImportedEvent {
	api_id: string;
	source?: 'catalog' | 'upload' | 'unknown';
}

type Listener = (event: ApiImportedEvent) => void;

let broadcast: BroadcastChannel | null = null;
let target: EventTarget | null = null;

function ensureBroadcast(): BroadcastChannel | null {
	if (typeof window === 'undefined') return null;
	if (broadcast) return broadcast;
	const Ctor = (window as unknown as { BroadcastChannel?: typeof BroadcastChannel })
		.BroadcastChannel;
	if (!Ctor) return null;
	try {
		broadcast = new Ctor(CHANNEL_NAME);
	} catch {
		broadcast = null;
	}
	return broadcast;
}

function ensureTarget(): EventTarget {
	if (target) return target;
	target = new EventTarget();
	return target;
}

export function emitApiImported(event: ApiImportedEvent): void {
	const bc = ensureBroadcast();
	const t = ensureTarget();
	t.dispatchEvent(new CustomEvent(EVENT_TYPE, { detail: event }));
	if (bc) {
		try {
			bc.postMessage({ type: EVENT_TYPE, payload: event });
		} catch {
			// closed / restricted — already covered by the EventTarget path.
		}
	}
}

export function subscribeApiImported(listener: Listener): () => void {
	const bc = ensureBroadcast();
	const t = ensureTarget();

	const onLocal = (e: Event) => {
		const detail = (e as CustomEvent<ApiImportedEvent>).detail;
		if (detail) listener(detail);
	};
	t.addEventListener(EVENT_TYPE, onLocal);

	let onMessage: ((e: MessageEvent) => void) | null = null;
	if (bc) {
		onMessage = (e: MessageEvent) => {
			if (e.data?.type === EVENT_TYPE && e.data.payload) {
				listener(e.data.payload as ApiImportedEvent);
			}
		};
		bc.addEventListener('message', onMessage);
	}

	return () => {
		t.removeEventListener(EVENT_TYPE, onLocal);
		if (bc && onMessage) bc.removeEventListener('message', onMessage);
	};
}

/** Test-only escape hatch — drops singletons so re-import is clean. */
export function __resetApiImportedEventChannelForTests(): void {
	try {
		broadcast?.close?.();
	} catch {
		// ignore
	}
	broadcast = null;
	target = null;
}
