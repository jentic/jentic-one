/**
 * Unit specs for the setup queue's state machine. The two rules worth pinning
 * without a DOM: `reuse` items must be bound before anything needing attention
 * (otherwise "reuse bypasses the queue" is a lie), and a non-terminal item must
 * come back out so re-entry can finish it — there is no `Skip for now`.
 */
import { describe, it, expect } from 'vitest';
import {
	QUEUE_STATUS_LABELS,
	activeEntry,
	buildQueue,
	dropWarning,
	isTerminal,
	markActive,
	needsPane,
	patchEntry,
	queueSummary,
	queueSummaryLine,
	retryEntry,
	unfinishedItems,
	type QueueEntry,
} from '@/modules/agents/lib/setupQueue';
import type { PreflightItem, PreflightOutcome } from '@/modules/agents/lib/apiPreflight';
import { CredentialType, type Credential, type SelectedApi } from '@/shared/credentials/api';

function makeCredential(over: Partial<Credential> = {}): Credential {
	return {
		credential_id: 'cred_1',
		name: 'Stripe — Production',
		type: CredentialType.BEARER_TOKEN,
		provider: 'static',
		api: { vendor: 'stripe.com', name: 'main', version: '1.0.0' },
		active: true,
		details: { hint: '••••' },
		provider_account_ref: null,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: null,
		...over,
	};
}

function makeItem(vendor: string, outcome: PreflightOutcome, over: Partial<PreflightItem> = {}) {
	const api: SelectedApi = {
		source: 'local',
		vendor,
		name: 'main',
		version: '1.0.0',
		label: vendor,
	};
	const candidates =
		over.candidates ?? (outcome === 'reuse' || outcome === 'oauth' ? [makeCredential()] : []);
	return {
		key: `${vendor}/main`,
		api,
		outcome,
		candidates,
		covering: candidates,
		importsApi: false,
		...over,
	} satisfies PreflightItem;
}

/** Shorthand for the queue's shape: `[key, status]` per entry, in order. */
function shape(entries: QueueEntry[]): [string, string][] {
	return entries.map((e) => [e.key, e.status]);
}

describe('buildQueue', () => {
	it('binds the free reuses first and keeps pick order within each group', () => {
		// Reuse needs no attention, so it must not wait behind a form the operator
		// is still typing — that ordering is what makes a mostly-reuse batch feel
		// like one click.
		const entries = buildQueue([
			makeItem('slack.com', 'form'),
			makeItem('stripe.com', 'reuse'),
			makeItem('notion.so', 'choose'),
			makeItem('github.com', 'reuse'),
		]);
		expect(entries.map((e) => e.key)).toEqual([
			'stripe.com/main',
			'github.com/main',
			'slack.com/main',
			'notion.so/main',
		]);
		expect(entries.every((e) => e.status === 'waiting')).toBe(true);
	});

	it('carries the preflight facts each item needs to finish', () => {
		const [entry] = buildQueue([
			makeItem('stripe.com', 'reuse', { importsApi: true, candidates: [makeCredential()] }),
		]);
		expect(entry.outcome).toBe('reuse');
		expect(entry.importsApi).toBe(true);
		expect(entry.candidates.map((c) => c.credential_id)).toEqual(['cred_1']);
	});

	it('an empty batch is a finished queue, not a stuck one', () => {
		const entries = buildQueue([]);
		expect(activeEntry(entries)).toBeNull();
		expect(queueSummary(entries).done).toBe(true);
	});
});

describe('needsPane', () => {
	it('is true for everything except a reuse', () => {
		expect(needsPane('reuse')).toBe(false);
		expect(needsPane('oauth')).toBe(true);
		expect(needsPane('choose')).toBe(true);
		expect(needsPane('form')).toBe(true);
	});
});

describe('isTerminal', () => {
	it('treats a failure as terminal so the items behind it are not stalled', () => {
		expect(isTerminal('added')).toBe(true);
		expect(isTerminal('dropped')).toBe(true);
		expect(isTerminal('failed')).toBe(true);
		expect(isTerminal('waiting')).toBe(false);
		expect(isTerminal('active')).toBe(false);
		expect(isTerminal('working')).toBe(false);
	});
});

describe('advancing the queue', () => {
	it('walks to the next unfinished item as each one lands', () => {
		let entries = buildQueue([makeItem('stripe.com', 'reuse'), makeItem('slack.com', 'form')]);
		expect(activeEntry(entries)?.key).toBe('stripe.com/main');

		entries = patchEntry(entries, 'stripe.com/main', {
			status: 'added',
			credentialId: 'cred_1',
		});
		expect(activeEntry(entries)?.key).toBe('slack.com/main');

		entries = patchEntry(entries, 'slack.com/main', { status: 'dropped' });
		expect(activeEntry(entries)).toBeNull();
	});

	it('steps over a failure instead of retrying it forever', () => {
		let entries = buildQueue([makeItem('stripe.com', 'reuse'), makeItem('slack.com', 'form')]);
		entries = patchEntry(entries, 'stripe.com/main', {
			status: 'failed',
			error: 'Bind failed',
		});
		expect(activeEntry(entries)?.key).toBe('slack.com/main');
	});

	it('a retry puts the failed item back in line, clearing its error', () => {
		let entries = buildQueue([makeItem('stripe.com', 'reuse'), makeItem('slack.com', 'form')]);
		entries = patchEntry(entries, 'stripe.com/main', {
			status: 'failed',
			error: 'Bind failed',
		});
		entries = retryEntry(entries, 'stripe.com/main');
		expect(activeEntry(entries)?.key).toBe('stripe.com/main');
		expect(entries[0].error).toBeUndefined();
	});

	it('patching one entry leaves the others alone', () => {
		const entries = buildQueue([
			makeItem('stripe.com', 'reuse'),
			makeItem('slack.com', 'form'),
		]);
		const next = patchEntry(entries, 'slack.com/main', { status: 'added' });
		expect(next[0]).toBe(entries[0]);
		expect(next[1].status).toBe('added');
	});

	it('markActive promotes only the live item, and is a no-op once it has', () => {
		const entries = markActive(
			buildQueue([makeItem('stripe.com', 'reuse'), makeItem('slack.com', 'form')]),
		);
		expect(shape(entries)).toEqual([
			['stripe.com/main', 'active'],
			['slack.com/main', 'waiting'],
		]);
		// Idempotent by identity, so it is safe to run on every render.
		expect(markActive(entries)).toBe(entries);
	});

	it('markActive does not disturb an item already in flight', () => {
		const entries = patchEntry(
			buildQueue([makeItem('stripe.com', 'reuse')]),
			'stripe.com/main',
			{
				status: 'working',
			},
		);
		expect(markActive(entries)).toBe(entries);
	});
});

describe('queueSummary', () => {
	it('separates what landed from what still owes the operator work', () => {
		let entries = buildQueue([
			makeItem('stripe.com', 'reuse'),
			makeItem('github.com', 'reuse'),
			makeItem('slack.com', 'form'),
			makeItem('notion.so', 'choose'),
		]);
		entries = patchEntry(entries, 'stripe.com/main', { status: 'added' });
		entries = patchEntry(entries, 'github.com/main', { status: 'failed', error: 'nope' });
		entries = patchEntry(entries, 'slack.com/main', { status: 'dropped' });

		expect(queueSummary(entries)).toEqual({
			added: 1,
			dropped: 1,
			failed: 1,
			unfinished: 1,
			// The failure is owed back alongside the unwalked item.
			remaining: 2,
			total: 4,
			done: false,
		});
	});

	it('is done only when every item is attached or declined', () => {
		let entries = buildQueue([makeItem('stripe.com', 'reuse')]);
		expect(queueSummary(entries).done).toBe(false);
		entries = patchEntry(entries, 'stripe.com/main', { status: 'added' });
		expect(queueSummary(entries).done).toBe(true);
	});

	it('is NOT done while an item is failed — a failure is outstanding work', () => {
		// The footer reads `Done` off this flag and suppresses the "the remaining N
		// wait here" note, so counting a failure as finished is what lets an API the
		// operator picked vanish on close.
		let entries = buildQueue([makeItem('stripe.com', 'reuse'), makeItem('github.com', 'form')]);
		entries = patchEntry(entries, 'stripe.com/main', { status: 'added' });
		entries = patchEntry(entries, 'github.com/main', { status: 'failed', error: 'nope' });
		const summary = queueSummary(entries);
		expect(summary.done).toBe(false);
		expect(summary.remaining).toBe(1);
	});
});

describe('queueSummaryLine', () => {
	it('reads as a plain outcome, pluralised on the added count', () => {
		expect(queueSummaryLine(queueSummary(buildQueue([])))).toBe('');
		expect(
			queueSummaryLine({
				added: 1,
				dropped: 0,
				failed: 0,
				unfinished: 0,
				remaining: 0,
				total: 1,
				done: true,
			}),
		).toBe('1 API added');
		expect(
			queueSummaryLine({
				added: 2,
				dropped: 1,
				failed: 1,
				unfinished: 0,
				remaining: 1,
				total: 4,
				done: false,
			}),
		).toBe('2 APIs added · 1 dropped · 1 failed');
	});
});

describe('dropWarning', () => {
	it('says the API is simply not added, with no promise of later', () => {
		// There is no deferred state to fall into, so the copy cannot imply one.
		expect(dropWarning('Stripe')).toBe("Stripe won't be added.");
	});
});

describe('unfinishedItems', () => {
	it('hands the remainder back in preflight shape so re-entry can resume it', () => {
		let entries = buildQueue([
			makeItem('stripe.com', 'reuse'),
			makeItem('slack.com', 'form'),
			makeItem('notion.so', 'choose', { candidates: [makeCredential(), makeCredential()] }),
		]);
		entries = patchEntry(entries, 'stripe.com/main', { status: 'added' });

		const remaining = unfinishedItems(entries);
		expect(remaining.map((i) => [i.key, i.outcome])).toEqual([
			['slack.com/main', 'form'],
			['notion.so/main', 'choose'],
		]);
		// The candidates travel with the item; the queue does not re-read the
		// credential list to resume.
		expect(remaining[1].candidates).toHaveLength(2);
	});

	it('includes failures — `Try again` dies with the sheet, so they are owed back', () => {
		// Nobody chose the failure and the in-place retry is gone once the sheet
		// closes, so a dropped-on-close failure means the operator picked an API and
		// it is silently never attached.
		let entries = buildQueue([makeItem('stripe.com', 'reuse')]);
		entries = patchEntry(entries, 'stripe.com/main', { status: 'failed', error: 'nope' });
		expect(unfinishedItems(entries).map((i) => i.key)).toEqual(['stripe.com/main']);
	});

	it('excludes both settled states — attached and declined are answers', () => {
		let entries = buildQueue([makeItem('stripe.com', 'reuse'), makeItem('slack.com', 'form')]);
		entries = patchEntry(entries, 'stripe.com/main', { status: 'added' });
		entries = patchEntry(entries, 'slack.com/main', { status: 'dropped' });
		expect(unfinishedItems(entries)).toEqual([]);
	});

	it('returns the in-flight item too — a closed pane finished nothing', () => {
		const entries = patchEntry(buildQueue([makeItem('slack.com', 'form')]), 'slack.com/main', {
			status: 'working',
		});
		expect(unfinishedItems(entries).map((i) => i.key)).toEqual(['slack.com/main']);
	});
});

describe('QUEUE_STATUS_LABELS', () => {
	it('never calls a dropped item "skipped"', () => {
		expect(QUEUE_STATUS_LABELS.dropped).toBe('Not added');
		expect(Object.values(QUEUE_STATUS_LABELS).join(' ')).not.toMatch(/skip/i);
	});
});
