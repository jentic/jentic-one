/**
 * Keep MSW's Service Worker mocking every test file for the whole browser-mode run.
 *
 * `public/mockServiceWorker.js` handles each client message inside
 * `event.waitUntil(sendToClient(...))` and waits for a reply on the message's
 * port. The MSW client never replies to the handshake messages below, so every
 * keepalive (one per file every 5s) leaves an event that never settles.
 * Chromium terminates a worker once an event has been pending for five
 * minutes, and the restarted worker has lost `activeClientIds`, the set of
 * clients that turned mocking on. A file that was already running at that point
 * is no longer mocked: its requests go through to the Vite server, or the
 * worker hands them to another file's frame and that file's handlers answer.
 * Its remaining tests fail on every retry, often by timing out on a `findBy*`.
 * CI runs the suite for more than five minutes, so whichever file is running
 * at that point loses its mocks.
 *
 * Two guards:
 * - `acknowledgeWorkerHandshakes` replies to those handshake messages so their
 *   events settle and no longer hold the worker past the five-minute limit.
 * - `reactivateMocking` re-registers this frame before each test. If the
 *   worker restarts anyway (an event from a frame that has already been torn
 *   down can still time out), the next test, or the retry, is mocked again.
 */

/** Worker → client messages the worker waits on but MSW never answers. */
const HANDSHAKE_MESSAGES = new Set([
	'KEEPALIVE_RESPONSE',
	'MOCKING_ENABLED',
	'INTEGRITY_CHECK_RESPONSE',
	'CLIENT_CLOSED',
]);

function messageType(event: MessageEvent): unknown {
	const data: unknown = event.data;
	return typeof data === 'object' && data !== null && 'type' in data ? data.type : undefined;
}

/** Reply to the worker's handshake messages so its `waitUntil` promises settle. */
export function acknowledgeWorkerHandshakes(): void {
	navigator.serviceWorker.addEventListener('message', (event) => {
		const type = messageType(event);
		if (typeof type === 'string' && HANDSHAKE_MESSAGES.has(type)) {
			event.ports[0]?.postMessage({});
		}
	});
}

/**
 * Register this frame with the worker again and wait until the worker
 * confirms. The worker keeps its clients in a `Set`, so this is a no-op while
 * the worker is alive and restores mocking after it has restarted.
 */
export async function reactivateMocking(): Promise<void> {
	const { active } = await navigator.serviceWorker.ready;
	if (!active) return;
	await new Promise<void>((resolve) => {
		const onMessage = (event: MessageEvent) => {
			if (event.source !== active || messageType(event) !== 'MOCKING_ENABLED') return;
			navigator.serviceWorker.removeEventListener('message', onMessage);
			resolve();
		};
		navigator.serviceWorker.addEventListener('message', onMessage);
		active.postMessage('MOCK_ACTIVATE');
	});
}
