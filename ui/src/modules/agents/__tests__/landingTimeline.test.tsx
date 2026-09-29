import { describe, it, expect, afterEach } from 'vitest';
import { act, render, screen, userEvent, waitFor } from '@/__tests__/test-utils';
import { foldFrames, type Script } from '@/modules/agents/components/landing/timeline';
import {
	useLandingTimeline,
	type LandingTimelineOptions,
} from '@/modules/agents/components/landing/useLandingTimeline';
import {
	WHY_LOOP_FROM,
	WHY_SCRIPT,
	WHY_TASK,
} from '@/modules/agents/components/landing/act1/script';
import { SETUP_SCRIPT, SETUP_STEPS } from '@/modules/agents/components/landing/act2/script';

/** A tiny script: each beat appends its id. Short beats so play is quick. */
const SCRIPT: Script<string[]> = {
	initial: [],
	beats: ['a', 'b', 'c', 'd'].map((id) => ({
		id,
		durationMs: 60,
		caption: `beat ${id}`,
		apply: (s: string[]) => [...s, id],
	})),
};

function Probe(opts: LandingTimelineOptions) {
	const t = useLandingTimeline(SCRIPT, opts);
	return (
		<div ref={t.stageRef}>
			<output data-testid="state">{t.state.join(',')}</output>
			<output data-testid="playing">{String(t.playing)}</output>
			<output data-testid="running">{String(t.running)}</output>
			<button type="button" onClick={t.pause}>
				pause
			</button>
			<button type="button" onClick={t.play}>
				play
			</button>
			<button type="button" onClick={t.next}>
				next
			</button>
			<button type="button" onClick={t.prev}>
				prev
			</button>
			<button type="button" onClick={() => t.seek('c')}>
				seek-c
			</button>
			<button type="button" onClick={t.restart}>
				restart
			</button>
			<button type="button" data-stage-control="">
				stage control
			</button>
		</div>
	);
}

const text = (id: string) => screen.getByTestId(id).textContent;

/**
 * Wait `n` animation frames: the clock's own tick. Enough of them to play
 * the whole tiny script several times over, had the clock been running.
 */
async function frames(n = 40) {
	for (let i = 0; i < n; i++) await new Promise((r) => requestAnimationFrame(() => r(null)));
}

/** Act 1's state after beats `0..i`. */
const WHY_FRAMES = foldFrames(WHY_SCRIPT);
const whyAt = (i: number) => WHY_FRAMES[i];
const whyEnd = WHY_FRAMES[WHY_FRAMES.length - 1];

function setVisibility(state: 'visible' | 'hidden') {
	Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
	document.dispatchEvent(new Event('visibilitychange'));
}

afterEach(() => {
	// Drop the instance override; the prototype getter answers again.
	delete (document as unknown as { visibilityState?: string }).visibilityState;
});

describe('timeline folding', () => {
	it('seeking folds to the same state as playing through', () => {
		const folded = foldFrames(SCRIPT);
		expect(folded.map((f) => f.join(''))).toEqual(['a', 'ab', 'abc', 'abcd']);
		// Each frame is the previous one with its beat applied.
		for (let i = 1; i < folded.length; i++)
			expect(SCRIPT.beats[i].apply(folded[i - 1])).toEqual(folded[i]);
	});
});

describe('useLandingTimeline', () => {
	it('autoplays through every beat and stops at the end', async () => {
		render(<Probe />);
		expect(text('state')).toBe('a');
		await waitFor(() => expect(text('state')).toBe('a,b,c,d'), { timeout: 3000 });
		await waitFor(() => expect(text('playing')).toBe('false'));
	});

	it('pauses, seeks, steps and restarts deterministically', async () => {
		const user = userEvent.setup();
		render(<Probe autoplay={false} />);
		expect(text('playing')).toBe('false');
		await user.click(screen.getByRole('button', { name: 'seek-c' }));
		expect(text('state')).toBe('a,b,c');
		await user.click(screen.getByRole('button', { name: 'prev' }));
		expect(text('state')).toBe('a,b');
		await user.click(screen.getByRole('button', { name: 'next' }));
		await user.click(screen.getByRole('button', { name: 'next' }));
		expect(text('state')).toBe('a,b,c,d');
		// Next past the end stays on the end.
		await user.click(screen.getByRole('button', { name: 'next' }));
		expect(text('state')).toBe('a,b,c,d');
		await user.click(screen.getByRole('button', { name: 'restart' }));
		expect(text('state')).toBe('a');
		expect(text('playing')).toBe('true');
	});

	it('holds the clock while the tab is hidden and resumes when it returns', async () => {
		render(<Probe />);
		act(() => setVisibility('hidden'));
		expect(text('running')).toBe('false');
		const before = text('state');
		await frames();
		expect(text('state')).toBe(before);
		act(() => setVisibility('visible'));
		await waitFor(() => expect(text('state')).toBe('a,b,c,d'), { timeout: 3000 });
	});

	it('under reduced motion never autoplays: stills are stepped by hand', async () => {
		const user = userEvent.setup();
		render(<Probe reducedMotion />);
		expect(text('playing')).toBe('false');
		await user.click(screen.getByRole('button', { name: 'play' }));
		expect(text('playing')).toBe('false');
		await frames();
		expect(text('state')).toBe('a');
		await user.click(screen.getByRole('button', { name: 'next' }));
		expect(text('state')).toBe('a,b');
	});

	it('holds the clock while focus is inside the stage', async () => {
		render(<Probe loop />);
		act(() => screen.getByRole('button', { name: 'next' }).focus());
		await waitFor(() => expect(text('running')).toBe('false'));
		const held = text('state');
		await frames();
		expect(text('state')).toBe(held);
	});

	it.each([
		['pauseOnFocus is off', { pauseOnFocus: false }, 'next'],
		['focus is on a stage control', {}, 'stage control'],
	] as const)('keeps playing with focus inside the stage when %s', async (_, opts, name) => {
		render(<Probe {...opts} loop />);
		act(() => screen.getByRole('button', { name }).focus());
		// The clock visibly advances with focus still there.
		const seen = new Set<string>();
		await waitFor(
			() => {
				seen.add(text('state') ?? '');
				expect(seen.size).toBeGreaterThan(2);
			},
			{ timeout: 3000 },
		);
		expect(document.activeElement).toBe(screen.getByRole('button', { name }));
		expect(text('running')).toBe('true');
	});

	it('loops continuously from `loopFrom`, never ending in a stopped state', async () => {
		render(<Probe loop loopFrom={1} />);
		await waitFor(() => expect(text('state')).toBe('a,b,c,d'), { timeout: 3000 });
		// Wraps past the one-time intro (beat a) and keeps playing.
		await waitFor(() => expect(text('state')).toBe('a,b'), { timeout: 3000 });
		expect(text('playing')).toBe('true');
		await waitFor(() => expect(text('state')).toBe('a,b,c'), { timeout: 3000 });
	});
});

describe('the act scripts', () => {
	it('Act 1 ends with two approvals, five traced calls, one stopped and no files lost', () => {
		const end = whyEnd;
		expect(end.stats).toEqual({ approvals: 2, traced: 5, stopped: 1 });
		expect(end.settled).toBe(true);
		expect(end.agentStatus).toBe('disabled');
		expect(end.bell).toEqual([]);
		expect(end.plan).toMatchObject({
			github: 'ok',
			gmail: 'ok',
			slack: 'ok',
			googledrive: 'fail',
		});
		expect(end.connected.gmail).toBe(true);
		// The log speaks the Monitor's source vocabulary only.
		expect(new Set(end.log.map((r) => r.source))).toEqual(new Set(['calls', 'audit']));
		expect(end.log.find((r) => r.tone === 'fail')).toMatchObject({
			title: 'DELETE drive /files',
			api: 'googledrive',
			detail: 'denied',
		});
		expect(end.withoutLost).toBe(248);
	});

	it('Act 1 puts both approvals in the bell: the self-registered agent, then the Gmail connection', () => {
		const bellTitles = WHY_SCRIPT.beats.map((_, i) => whyAt(i).bell.map((b) => b.title)).flat();
		expect([...new Set(bellTitles)]).toEqual([
			'research-bot wants to join',
			'research-bot asks to connect Gmail',
		]);
	});

	it('Act 1 stage cards only ever resolve to allow/deny/waiting tones', () => {
		for (let i = 0; i < WHY_SCRIPT.beats.length; i++) {
			const s = whyAt(i);
			for (const card of Object.values(s.cards))
				expect(['idle', 'active', 'ok', 'warn', 'fail']).toContain(card.tone);
		}
	});

	it('Act 1 opens on the typed task, and the WITHOUT lane records every outcome', () => {
		expect(WHY_SCRIPT.beats[0].id).toBe('task');
		expect(whyAt(0).intro).toBe(true);
		expect(whyAt(1).intro).toBe(false);
		expect(whyEnd.without).toEqual({
			github: 'read with a raw token — no record',
			gmail: 'someone pastes a new key into .env',
			slack: 'posted — nobody approved it',
			googledrive: '248 files deleted, including shared ones',
		});
	});

	it('Act 1 types the everyday task, then loops past the intro', () => {
		// The task asks for each step of the plan, in the plan's order.
		expect(WHY_TASK).toMatch(/GitHub.+email.+Slack.+Drive/);
		expect(WHY_SCRIPT.beats[WHY_LOOP_FROM].id).toBe('register');
		// Every beat holds long enough to read.
		for (const b of WHY_SCRIPT.beats) expect(b.durationMs).toBeGreaterThanOrEqual(1000);
		const total = WHY_SCRIPT.beats.reduce((n, b) => n + b.durationMs, 0);
		expect(total).toBeGreaterThanOrEqual(70_000);
		expect(total).toBeLessThanOrEqual(90_000);
	});

	it('Act 1 pauses the agent, not the story: the pause beat is followed by more story', () => {
		const i = WHY_SCRIPT.beats.findIndex((b) => b.id === 'disable');
		const s = whyAt(i);
		expect(s.agentStatus).toBe('disabled');
		expect(s.callout).toMatchObject({ kind: 'paused', to: 'agent' });
		expect(i).toBeLessThan(WHY_SCRIPT.beats.length - 1);
	});

	it('Act 1 points at the Monitor exactly when an event lands there, one at a time', () => {
		const kinds = WHY_FRAMES.map((s) => s.callout?.kind ?? null);
		const at = (id: string) => kinds[WHY_SCRIPT.beats.findIndex((b) => b.id === id)];
		expect(at('register')).toBe('needs');
		expect(at('gmail-ask')).toBe('needs');
		expect(at('github-record')).toBe('recorded');
		expect(at('drive-deny')).toBe('denied');
		expect(at('disable')).toBe('paused');
		expect(at('approve')).toBe('recorded');
		expect(at('gmail-connect')).toBe('recorded');
		// A callout never carries over into the next beat.
		expect(at('github-land')).toBeNull();
		expect(at('end')).toBeNull();
	});

	it('Act 1 counters move on the same beat as their Monitor event, never lagging', () => {
		for (let i = 0; i < WHY_SCRIPT.beats.length; i++) {
			const s = whyAt(i);
			// Every traced call has exactly one call row in Activity, at every beat.
			expect(s.stats.traced).toBe(s.log.filter((r) => r.source === 'calls').length);
			// A stopped call is always one of the traced ones.
			expect(s.stats.stopped).toBeLessThanOrEqual(s.stats.traced);
			expect(s.stats.stopped).toBe(s.log.filter((r) => r.tone === 'fail').length);
		}
		const deny = WHY_SCRIPT.beats.findIndex((b) => b.id === 'drive-deny');
		expect(whyAt(deny).stats).toMatchObject({ traced: 5, stopped: 1 });
	});

	it('Act 1 lands each call row in Activity on its Record beat, never before', () => {
		for (const key of ['github', 'gmail-retry', 'slack']) {
			const at = WHY_SCRIPT.beats.findIndex((b) => b.id === `${key}-record`);
			const before = whyAt(at - 1);
			const after = whyAt(at);
			expect(before.log.some((r) => r.id === `${key}-call`)).toBe(false);
			expect(after.log[0]).toMatchObject({ id: `${key}-call`, source: 'calls' });
			expect(after.pulse).toBe(`${key}-call`);
			expect(after.cards.record.tone).toBe('ok');
		}
	});

	it('Act 1 denies the Drive delete at the Rules card and still traces it', () => {
		const i = WHY_SCRIPT.beats.findIndex((b) => b.id === 'drive-record');
		const s = whyAt(i);
		expect(s.call).toMatchObject({ api: 'googledrive', method: 'DELETE', op: 'files.delete' });
		expect(s.cards.rules).toMatchObject({
			tone: 'fail',
			verdict: 'denied',
			lines: ['DELETE drive', 'rule: deny deletes'],
		});
		expect(s.cards.record).toMatchObject({ tone: 'ok', verdict: 'traced' });
		expect(s.cards.vault.tone).toBe('idle');
		expect(s.pointer).toBe('pause');
	});

	it('Act 2 walks all six steps in order', () => {
		const steps = foldFrames(SETUP_SCRIPT).map((s) => s.step);
		expect([...new Set(steps)]).toEqual(SETUP_STEPS.map((_, i) => i));
		expect(SETUP_STEPS.map((s) => s.id)).toEqual([
			'create',
			'apis',
			'keys',
			'rules',
			'connect',
			'govern',
		]);
	});
});
