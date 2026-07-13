import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import {
	CredentialType,
	OAUTH_CONNECT_MESSAGE_TYPE,
	runConnectFlow,
	type CredentialRedactedResponse,
} from '@/modules/credentials/api';
import {
	makeMockCredential,
	resetCredentialsStore,
	setConnectAutoCompletes,
} from '@/modules/credentials/mocks/handlers';

/**
 * `runConnectFlow` is the single opener-side chokepoint for the OAuth connect
 * round-trip. After PR #548 it learns the outcome by polling the credential;
 * #598 adds an *advisory* `postMessage` from the `/oauth/connected` popup page
 * so the opener can re-read immediately instead of waiting for the next poll
 * tick. These tests pin the message contract: the opener re-reads on a valid,
 * same-origin message, ignores foreign-origin messages, and never trusts the
 * message payload as the source of truth (the credentials API stays canonical).
 */
describe('runConnectFlow — advisory postMessage (#598)', () => {
	let fakePopup: { closed: boolean; close: () => void };

	beforeEach(() => {
		resetCredentialsStore();
		// We drive the "connected" transition by hand so the message — not the
		// mock's auto-complete timer — is what unblocks the wait.
		setConnectAutoCompletes(false);
		fakePopup = { closed: false, close: vi.fn() };
		vi.spyOn(window, 'open').mockReturnValue(fakePopup as unknown as Window);
	});

	afterEach(() => {
		vi.restoreAllMocks();
		resetCredentialsStore();
		setConnectAutoCompletes(true);
	});

	function seedConnectableCredential(): string {
		const cred = makeMockCredential({ type: CredentialType.OAUTH2, provider: 'direct_oauth2' });
		return cred.credential_id;
	}

	it('re-reads and resolves connected when a same-origin advisory message arrives', async () => {
		const id = seedConnectableCredential();
		// The "callback landed" effect: once the popup signals, the credential is
		// connected. We flip the mock to report a connection on the next read.
		let connected = false;
		worker.use(
			http.get('/credentials/:id', ({ params }) => {
				if (String(params.id) !== id) return undefined;
				// runConnectFlow only inspects provider_account_ref / updated_at to
				// decide "connected", so a minimal partial is enough here.
				return HttpResponse.json({
					credential_id: id,
					provider_account_ref: connected ? 'connected' : null,
					updated_at: connected ? new Date().toISOString() : null,
				} as Partial<CredentialRedactedResponse>);
			}),
		);

		// Start the flow with a long poll interval — if the message short-circuit
		// is broken, the test would hang past its timeout rather than resolve fast.
		const flow = runConnectFlow(id, { pollMs: 60_000, timeoutMs: 120_000 });

		// Simulate the popup completing then posting its advisory signal. Give the
		// flow time to begin (connect → initial read → attach the message
		// listener) before signalling; dispatch a few times to avoid a startup
		// race where the first message lands before the listener is attached.
		await new Promise((r) => setTimeout(r, 200));
		connected = true;
		for (let i = 0; i < 5; i += 1) {
			window.dispatchEvent(
				new MessageEvent('message', {
					origin: window.location.origin,
					data: { type: OAUTH_CONNECT_MESSAGE_TYPE, status: 'ok' },
				}),
			);
			await new Promise((r) => setTimeout(r, 50));
		}

		const outcome = await flow;
		expect(outcome.status).toBe('connected');
	});

	it('ignores a message from a foreign origin (no early resolve)', async () => {
		const id = seedConnectableCredential();
		worker.use(
			http.get('/credentials/:id', ({ params }) => {
				if (String(params.id) !== id) return undefined;
				// Never connects on its own — only an accepted message + flip would.
				return HttpResponse.json({
					credential_id: id,
					provider_account_ref: null,
					updated_at: null,
				} as Partial<CredentialRedactedResponse>);
			}),
		);

		const flow = runConnectFlow(id, { pollMs: 50, timeoutMs: 400 });

		await new Promise((r) => setTimeout(r, 10));
		// Wrong origin → must be ignored. Credential never flips, so the flow can
		// only end via the short timeout, proving the message did not short-circuit
		// it into a (false) connected.
		window.dispatchEvent(
			new MessageEvent('message', {
				origin: 'https://evil.example.com',
				data: { type: OAUTH_CONNECT_MESSAGE_TYPE, status: 'ok' },
			}),
		);

		const outcome = await flow;
		expect(outcome.status).toBe('timeout');
	});

	it('ignores a message whose source is not the popup we opened (no early resolve)', async () => {
		const id = seedConnectableCredential();
		worker.use(
			http.get('/credentials/:id', ({ params }) => {
				if (String(params.id) !== id) return undefined;
				return HttpResponse.json({
					credential_id: id,
					provider_account_ref: null,
					updated_at: null,
				} as Partial<CredentialRedactedResponse>);
			}),
		);

		const flow = runConnectFlow(id, { pollMs: 50, timeoutMs: 400 });

		await new Promise((r) => setTimeout(r, 30));
		// Same origin + correct type, but from an *unrelated* window (not our
		// popup). Hardening (#611 review L1) requires this be ignored, so an
		// unrelated tab/iframe can't pace our poll loop. Credential never flips,
		// so the only way out is the timeout. MessageEvent.source must be a real
		// window, so borrow an iframe's contentWindow as the foreign source.
		const iframe = document.createElement('iframe');
		document.body.appendChild(iframe);
		const otherWindow = iframe.contentWindow as Window;
		window.dispatchEvent(
			new MessageEvent('message', {
				origin: window.location.origin,
				source: otherWindow,
				data: { type: OAUTH_CONNECT_MESSAGE_TYPE, status: 'ok' },
			}),
		);

		const outcome = await flow;
		expect(outcome.status).toBe('timeout');
		iframe.remove();
	});

	it('does not busy-loop after an error message — the signal wakes the loop once, then pacing resumes', async () => {
		const id = seedConnectableCredential();
		// The credential never connects (mirrors an error outcome). Count how many
		// times the loop re-reads while it waits out the timeout.
		let reads = 0;
		worker.use(
			http.get('/credentials/:id', ({ params }) => {
				if (String(params.id) !== id) return undefined;
				reads += 1;
				return HttpResponse.json({
					credential_id: id,
					provider_account_ref: null,
					updated_at: null,
				} as Partial<CredentialRedactedResponse>);
			}),
		);

		// pollMs 50, timeout 600 → a correctly-paced loop reads ~12 times
		// (600/50) + the initial `before` read. A latched busy-loop would fire
		// hundreds of back-to-back requests. We post an error message early to
		// trip the (previously permanent) latch.
		const flow = runConnectFlow(id, { pollMs: 50, timeoutMs: 600 });

		await new Promise((r) => setTimeout(r, 20));
		window.dispatchEvent(
			new MessageEvent('message', {
				origin: window.location.origin,
				data: { type: OAUTH_CONNECT_MESSAGE_TYPE, status: 'error' },
			}),
		);

		const outcome = await flow;
		expect(outcome.status).toBe('timeout');
		// Generous ceiling: correct pacing is ~13 reads; a busy-loop would be
		// orders of magnitude higher. 40 leaves slack for timing jitter while
		// still failing hard on a regression to the latched behaviour.
		expect(reads).toBeLessThan(40);
	});
});
