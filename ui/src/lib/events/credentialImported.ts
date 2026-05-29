/**
 * Cross-tab event channel for credential lifecycle (P8).
 *
 * Goal: when a user adds a credential while Discover is open in
 * another tab/window, surface a toast in Discover so the silent
 * lazy-import path stops being silent. Modelled as a tiny
 * publish/subscribe so consumers don't have to know whether the
 * underlying transport is `BroadcastChannel` (cross-tab) or an
 * in-page `EventTarget` (single-tab fallback).
 *
 * Same-tab consumers also get the event — both transports fire on the
 * tab that emitted, so the single Discover listener handles both
 * cross-tab and same-window flows uniformly.
 */

const CHANNEL_NAME = 'jentic.credentials';
const EVENT_TYPE = 'credentialImported';

export interface CredentialImportedEvent {
	api_id: string;
	workflow_count?: number;
	operation_count?: number;
}

type Listener = (event: CredentialImportedEvent) => void;

// Lazy singletons so consumers can import this module from
// non-browser test environments (e.g. Node setup files) without
// constructing a BroadcastChannel at import time.
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

export function emitCredentialImported(event: CredentialImportedEvent): void {
	const bc = ensureBroadcast();
	const t = ensureTarget();

	// Fire same-tab via EventTarget regardless of BroadcastChannel
	// availability — BroadcastChannel doesn't deliver to the sender's
	// own listeners by spec, so we'd miss the same-tab case otherwise.
	t.dispatchEvent(new CustomEvent(EVENT_TYPE, { detail: event }));

	if (bc) {
		try {
			bc.postMessage({ type: EVENT_TYPE, payload: event });
		} catch {
			// closed / restricted — already covered by the EventTarget path.
		}
	}
}

export function subscribeCredentialImported(listener: Listener): () => void {
	const bc = ensureBroadcast();
	const t = ensureTarget();

	const onLocal = (e: Event) => {
		const detail = (e as CustomEvent<CredentialImportedEvent>).detail;
		if (detail) listener(detail);
	};
	t.addEventListener(EVENT_TYPE, onLocal);

	let onMessage: ((e: MessageEvent) => void) | null = null;
	if (bc) {
		onMessage = (e: MessageEvent) => {
			if (e.data?.type === EVENT_TYPE && e.data.payload) {
				listener(e.data.payload as CredentialImportedEvent);
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
export function __resetCredentialEventChannelForTests(): void {
	try {
		broadcast?.close?.();
	} catch {
		// ignore
	}
	broadcast = null;
	target = null;
}
