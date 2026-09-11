import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { worker } from '@/mocks/browser';
import {
	CredentialType,
	OAUTH_CONNECT_MESSAGE_TYPE,
	runConnectFlow,
	type CredentialRedactedResponse,
} from '@/shared/credentials/api';
import {
	makeMockCredential,
	resetCredentialsStore,
	setConnectAutoCompletes,
} from '@/shared/credentials/mocks/handlers';

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

/**
 * The device-code branch of ``runConnectFlow`` is a distinct path: no
 * popup is ever opened (the human types a ``user_code`` at the vendor's
 * verification URI), and completion is driven by the backend
 * ``ConnectPollScanner`` rather than the OAuth callback route. These
 * tests pin the three load-bearing behaviours:
 *
 *   * a device-code challenge WITHOUT a caller-supplied render hook
 *     returns ``unsupported_challenge`` immediately — polling in silence
 *     would look like a hang from the user's perspective;
 *   * WITH a render hook, the flow polls the credentials API for
 *     completion just like auth-code — no popup, no ``window.open`` at all;
 *   * the cleanup returned from the render hook fires exactly once, on
 *     every terminal outcome (connected / timeout).
 */
describe('runConnectFlow — device-code branch', () => {
	beforeEach(() => {
		resetCredentialsStore();
		setConnectAutoCompletes(false);
	});

	afterEach(() => {
		vi.restoreAllMocks();
		resetCredentialsStore();
		setConnectAutoCompletes(true);
	});

	function seedDeviceCodeCredential(): string {
		const cred = makeMockCredential({
			type: CredentialType.OAUTH2,
			provider: 'device_authorization',
		});
		return cred.credential_id;
	}

	function stubDeviceCodeConnectResponse(id: string): void {
		worker.use(
			http.post('/credentials/:id/connect', ({ params }) => {
				if (String(params.id) !== id) return undefined;
				return HttpResponse.json({
					kind: 'device_authorization',
					user_code: 'ABCD-1234',
					verification_uri: 'https://idp.example.com/device',
					verification_uri_complete: 'https://idp.example.com/device?user_code=ABCD-1234',
					poll_interval_seconds: 5,
				});
			}),
		);
	}

	it('returns unsupported_challenge when no render hook is provided', async () => {
		const id = seedDeviceCodeCredential();
		stubDeviceCodeConnectResponse(id);
		const windowOpen = vi.spyOn(window, 'open');

		const outcome = await runConnectFlow(id, { pollMs: 20, timeoutMs: 300 });

		expect(outcome.status).toBe('unsupported_challenge');
		// No popup on the device-code path — that's the whole point of the
		// render-hook contract.
		expect(windowOpen).not.toHaveBeenCalled();
	});

	it('invokes the render hook, polls until connected, and never opens a popup', async () => {
		const id = seedDeviceCodeCredential();
		stubDeviceCodeConnectResponse(id);
		const windowOpen = vi.spyOn(window, 'open');

		let connected = false;
		worker.use(
			http.get('/credentials/:id', ({ params }) => {
				if (String(params.id) !== id) return undefined;
				return HttpResponse.json({
					credential_id: id,
					provider_account_ref: connected ? 'connected' : null,
					updated_at: connected ? new Date().toISOString() : null,
				} as Partial<CredentialRedactedResponse>);
			}),
		);

		const rendered: Array<{ user_code: string }> = [];
		let cleanupCalls = 0;
		const flow = runConnectFlow(id, {
			pollMs: 50,
			timeoutMs: 3000,
			onDeviceAuthorizationChallenge: (challenge) => {
				rendered.push({ user_code: challenge.user_code });
				return () => {
					cleanupCalls += 1;
				};
			},
		});

		// Simulate the human approving at the vendor and the scanner
		// vaulting the token — the credential flips connected server-side.
		await new Promise((r) => setTimeout(r, 100));
		connected = true;

		const outcome = await flow;

		expect(outcome.status).toBe('connected');
		// The device-code render hook is called exactly once with the
		// user_code, and never at all opens a popup.
		expect(rendered).toEqual([{ user_code: 'ABCD-1234' }]);
		expect(windowOpen).not.toHaveBeenCalled();
		// The cleanup returned from the hook fires on the terminal outcome
		// so the caller can dismiss its modal / dialog.
		expect(cleanupCalls).toBe(1);
	});

	it('runs cleanup on timeout too', async () => {
		const id = seedDeviceCodeCredential();
		stubDeviceCodeConnectResponse(id);
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

		let cleanupCalls = 0;
		const outcome = await runConnectFlow(id, {
			pollMs: 30,
			timeoutMs: 200,
			onDeviceAuthorizationChallenge: () => {
				return () => {
					cleanupCalls += 1;
				};
			},
		});

		expect(outcome.status).toBe('timeout');
		// If cleanup ever stops firing on timeout, a stranded modal would
		// linger over the credentials page after the flow expires.
		expect(cleanupCalls).toBe(1);
	});
});
