/**
 * The held-call words the bell, the rail's Inbox and the attention toast
 * share: "<agent> wants to run <headline>", how long a call has waited, and how
 * long it has left.
 */
import { describe, it, expect } from 'vitest';
import { isRouteHeadline } from '@/shared/approvals/heldCall';
import { askPhrase, expiresIn, heldCallAsk, waitingSince } from '@/shared/approvals/heldCallCopy';

const NOW = Date.parse('2026-10-22T12:00:00Z');
const at = (offsetMs: number) => new Date(NOW + offsetMs).toISOString();
const MIN = 60_000;

describe('askPhrase', () => {
	it('quotes an operation’s name, whether it is a verb phrase or a noun phrase', () => {
		expect(askPhrase('Create a charge')).toBe('“Create a charge”');
		expect(askPhrase('Flight delays')).toBe('“Flight delays”');
		expect(heldCallAsk('billing-ops', 'Create a charge')).toBe(
			'billing-ops wants to run “Create a charge”',
		);
		expect(heldCallAsk('my-agent', 'Flight schedules')).toBe(
			'my-agent wants to run “Flight schedules”',
		);
	});

	it('keeps names and acronyms exactly as the spec writes them', () => {
		expect(askPhrase('GitHub merge')).toBe('“GitHub merge”');
		expect(askPhrase('PR merge')).toBe('“PR merge”');
	});

	it('leaves a route, or a spec path template, as it is', () => {
		expect(askPhrase('POST /v1/charges')).toBe('POST /v1/charges');
		expect(askPhrase('PUT /v4/spreadsheets/{spreadsheetId}/values/{range}')).toBe(
			'PUT /v4/spreadsheets/{spreadsheetId}/values/{range}',
		);
		expect(heldCallAsk('my-agent', 'PUT /v4/spreadsheets/{spreadsheetId}/values/{range}')).toBe(
			'my-agent wants to run PUT /v4/spreadsheets/{spreadsheetId}/values/{range}',
		);
	});
});

describe('isRouteHeadline', () => {
	it('tells a route from an operation’s name', () => {
		expect(isRouteHeadline('POST /v1/charges')).toBe(true);
		expect(isRouteHeadline('PUT /v4/spreadsheets/{spreadsheetId}/values/{range}')).toBe(true);
		expect(isRouteHeadline('Create a charge')).toBe(false);
		expect(isRouteHeadline('Post a comment')).toBe(false);
		expect(isRouteHeadline('GET started with the API')).toBe(false);
	});
});

describe('waitingSince', () => {
	it('reads just now, minutes, hours and days', () => {
		expect(waitingSince(at(-20_000), NOW)).toBe('just now');
		expect(waitingSince(at(-5 * MIN), NOW)).toBe('5m ago');
		expect(waitingSince(at(-128 * MIN), NOW)).toBe('2h ago');
		expect(waitingSince(at(-50 * 60 * MIN), NOW)).toBe('2d ago');
		expect(waitingSince('not a date', NOW)).toBe('');
	});
});

describe('expiresIn', () => {
	it("reads what is left of the window, in the review pop-up's units", () => {
		expect(expiresIn(at((21 * 60 + 51) * MIN + 10_000), NOW)).toBe('expires in 21 h');
		expect(expiresIn(at(40 * MIN), NOW)).toBe('expires in 40 min');
		expect(expiresIn(at(72 * 60 * MIN), NOW)).toBe('expires in 3 days');
		expect(expiresIn(at(20_000), NOW)).toBe('expires in 1 min');
		expect(expiresIn(at(-MIN), NOW)).toBe('expired');
		expect(expiresIn('not a date', NOW)).toBe('');
	});
});
