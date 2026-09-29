/**
 * Act 1's state shape and the small pure helpers its script folds with. The
 * scene (two lanes, the five stage cards, the operator panel) renders only
 * from a `WhyState`.
 */
import type {
	ActivityRow,
	BellItem,
	OperatorAgentStatus,
	OperatorPointer,
} from '@/modules/agents/components/landing/operator';
import {
	STORY_APIS,
	type DemoCall,
	type StoryApiId,
} from '@/modules/agents/components/landing/data/demoFixtures';

/** The five checks every call passes, in order. */
export const CHECKPOINTS = ['find', 'identity', 'rules', 'vault', 'record'] as const;
export type Checkpoint = (typeof CHECKPOINTS)[number];

export type CardTone = 'idle' | 'active' | 'ok' | 'warn' | 'fail';
export type PlanTone = 'todo' | 'active' | 'ok' | 'warn' | 'fail';

export interface CardState {
	tone: CardTone;
	/** Two short mono lines about the current call. */
	lines: [string, string];
	/** The verdict word under the divider ("found", "denied"). */
	verdict: string;
}

/**
 * An event landing in the Monitor: the call's badge flies from where it
 * happened (the API box it ended at, or the agent) across a short bridge into
 * the new Monitor item, and a short label names it there.
 */
export interface MonitorCallout {
	kind: 'recorded' | 'needs' | 'denied' | 'paused';
	/** The short label shown in the Monitor, above the landed item. */
	text: string;
	/** Where the badge lifts off: an API box, or the agent (via the lane's edge). */
	from: StoryApiId | 'agent';
	/** `data-monitor-anchor` of the Monitor item it lands on. */
	to: 'notifications' | 'activity' | 'agent';
	/** The call's step number, shown on its badge. */
	tag?: string;
}
export interface WhyState {
	/** The opening beat: the task is being typed to the agent. */
	intro: boolean;
	agentStatus: OperatorAgentStatus;
	plan: Record<StoryApiId, PlanTone>;
	call: DemoCall | null;
	/** The call dot: -1 at the agent, 0..4 on a card, 5 at the API. */
	dotAt: number;
	dotTone: 'default' | 'warn' | 'fail';
	/** A short mono label under the dot ("asking you", "403 denied"). */
	dotLabel: string;
	/** The key rides with the call once the vault has added it. */
	keyed: boolean;
	cards: Record<Checkpoint, CardState>;
	connected: Record<StoryApiId, boolean>;
	/** The API box a call last landed on, and how. */
	apiHit: Partial<Record<StoryApiId, 'ok' | 'fail'>>;
	/** The WITHOUT lane: the API its dot is flying to, and each API's outcome line. */
	withoutFiring: StoryApiId | null;
	without: Partial<Record<StoryApiId, string>>;
	withoutLost: number;
	/** The line under the WITH lane's title. */
	withNote: string;
	bell: BellItem[];
	log: ActivityRow[];
	pointer: OperatorPointer;
	/**
	 * The Activity row that just landed. Its row, the Record card and the API
	 * box pulse together, so the lane and the Monitor read as one event.
	 */
	pulse: string | null;
	/** Set on the beat an event lands in Monitor; every beat starts with none. */
	callout: MonitorCallout | null;
	stats: { approvals: number; traced: number; stopped: number };
	settled: boolean;
}

export const IDLE_CARD: CardState = { tone: 'idle', lines: ['', ''], verdict: '' };

export const IDLE_CARDS: Record<Checkpoint, CardState> = {
	find: IDLE_CARD,
	identity: IDLE_CARD,
	rules: IDLE_CARD,
	vault: IDLE_CARD,
	record: IDLE_CARD,
};

export type Fold = (s: WhyState) => WhyState;

export const compose =
	(...fns: Fold[]): Fold =>
	(s) =>
		fns.reduce((acc, f) => f(acc), s);

export const set =
	(patch: Partial<WhyState> | ((s: WhyState) => Partial<WhyState>)): Fold =>
	(s) => ({ ...s, ...(typeof patch === 'function' ? patch(s) : patch) });

/** Put the dot on card `i` and write the card. */
export function card(
	i: number,
	tone: CardTone,
	lines: [string, string],
	verdict: string,
	extra: Partial<WhyState> = {},
): Fold {
	return (s) => ({
		...s,
		dotAt: i,
		keyed: s.keyed || (CHECKPOINTS[i] === 'vault' && tone === 'ok'),
		cards: { ...s.cards, [CHECKPOINTS[i]]: { tone, lines, verdict } },
		pulse: null,
		...extra,
	});
}

/** A new call leaves the agent: cards cleared, its plan chip lit. */
export function launch(call: DemoCall, withoutLine?: string): Fold {
	return (s) => ({
		...s,
		call,
		dotAt: -1,
		dotTone: 'default',
		dotLabel: '',
		keyed: false,
		cards: IDLE_CARDS,
		apiHit: {},
		pulse: null,
		plan: { ...s.plan, [call.api]: 'active' },
		withoutFiring: withoutLine ? call.api : null,
	});
}

/** The WITHOUT dot lands: its outcome line appears. */
export function withoutLands(api: StoryApiId, line: string, lost = 0): Fold {
	return (s) => ({
		...s,
		withoutFiring: null,
		without: { ...s.without, [api]: line },
		withoutLost: s.withoutLost + lost,
	});
}

export const logRow =
	(row: ActivityRow): Fold =>
	(s) => ({ ...s, log: [row, ...s.log] });

/** The plan step a call belongs to, as its short tag ("1".."4"). */
export function callTag(call: DemoCall): string {
	return String(STORY_APIS.indexOf(call.api) + 1);
}

/**
 * Record completes: the trace is written, and at that same moment the call's
 * row lands in Activity (and pulses with the Record card).
 */
export function record(key: string, call: DemoCall, lines: [string, string], detail: string): Fold {
	const id = `${key}-call`;
	return compose(
		card(4, 'ok', lines, 'traced'),
		(s) => ({
			...s,
			pulse: id,
			callout: {
				kind: 'recorded',
				text: 'Recorded',
				from: call.api,
				to: 'activity',
				tag: callTag(call),
			},
			stats: { ...s.stats, traced: s.stats.traced + 1 },
		}),
		logRow({
			id,
			source: 'calls',
			tone: 'ok',
			statusLabel: 'Completed',
			title: `${call.method} ${call.op}`,
			api: call.api,
			detail,
			tag: callTag(call),
		}),
	);
}

/** The call reaches its API: the box lights, the plan step is done. */
export function land(call: DemoCall): Fold {
	return (s) => ({
		...s,
		dotAt: 5,
		pulse: null,
		apiHit: { [call.api]: 'ok' },
		plan: { ...s.plan, [call.api]: 'ok' },
	});
}
