/**
 * Act 1's script — research-bot's weekly summary job, told twice at once:
 * WITH Jentic One (every call walks the same five stage cards and lands in the
 * Monitor) and WITHOUT a gateway (every key in the agent's hands, nothing
 * asked, nothing recorded). Data only; the scene renders whatever state these
 * beats fold to. It loops: after the last beat it replays from `register`,
 * skipping the typed task.
 */
import type { Beat, Script } from '@/modules/agents/components/landing/timeline';
import {
	DEMO_AGENT,
	DEMO_CALLS,
	STORY_APIS,
	UNGUARDED_FILES_DELETED,
	type DemoCall,
	type StoryApiId,
} from '@/modules/agents/components/landing/data/demoFixtures';
import {
	IDLE_CARDS,
	callTag,
	card,
	compose,
	land,
	launch,
	logRow,
	record,
	set,
	withoutLands,
	type CardTone,
	type Fold,
	type WhyState,
} from '@/modules/agents/components/landing/act1/model';

/** What you ask research-bot to do; typed into the composer as the act opens. */
export const WHY_TASK =
	"Summarise this week's GitHub issues, email the summary to the team, share it in #eng on Slack, and clean up old files in Google Drive.";

/** Typing speed, and how long the finished message stays up before it sends. */
export const COMPOSER_MS_PER_CHAR = 24;
export const COMPOSER_HOLD_MS = 2500;
/** The intro beat: type the task, hold it to read, send (plus slack for mount). */
const TASK_BEAT_MS = WHY_TASK.length * COMPOSER_MS_PER_CHAR + COMPOSER_HOLD_MS + 700;

const DELETED = `${UNGUARDED_FILES_DELETED} files deleted, including shared ones`;

/** What each call gets WITHOUT a gateway: the consequence, in plain words. */
const WITHOUT_OUTCOMES = {
	github: 'read with a raw token — no record',
	gmail: 'someone pastes a new key into .env',
	slack: 'posted — nobody approved it',
	googledrive: DELETED,
} as const;

const WHY_INITIAL: WhyState = {
	intro: true,
	agentStatus: null,
	plan: { github: 'todo', gmail: 'todo', slack: 'todo', googledrive: 'todo' },
	call: null,
	dotAt: -1,
	dotTone: 'default',
	dotLabel: '',
	keyed: false,
	cards: IDLE_CARDS,
	connected: { github: true, gmail: false, slack: true, googledrive: true },
	apiHit: {},
	withoutFiring: null,
	without: {},
	withoutLost: 0,
	withNote: '',
	bell: [],
	log: [],
	pointer: 'none',
	pulse: null,
	callout: null,
	stats: { approvals: 0, traced: 0, stopped: 0 },
	settled: false,
};

/** Pacing: long enough to read every card, never sluggish. */
const T = {
	launch: 1100,
	check: 1500,
	quick: 1000,
	record: 2800,
	land: 1600,
};

/** A beat of Act 1; `api` names the plan step (the story API) it belongs to. */
interface WhyBeat extends Beat<WhyState> {
	api?: StoryApiId;
}

type Step = [id: string, ms: number, caption: string, apply: Fold, api?: StoryApiId];

// Every beat starts with no callout: one appears only on the beat its event lands.
const beat = ([id, durationMs, caption, apply, api]: Step): WhyBeat => ({
	id,
	durationMs,
	caption,
	apply: (s) => apply({ ...s, callout: null }),
	api,
});

type Lines = [string, string];

/** One stage card row: two detail lines, the verdict, optional tone. */
type CardRow = [lines: Lines, verdict: string, tone?: CardTone];

const KNOWN: Lines = [DEMO_AGENT.name, 'approved'];

/**
 * A call leaving the agent and clearing its first two cards (Find, Identity).
 * The WITHOUT lane's dot leaves at launch and its outcome lands with Find, a
 * beat later: no checks, no wait.
 */
function firstChecks(
	key: string,
	call: DemoCall,
	caption: string,
	found: Lines,
	withoutLine: string,
	{ ms = T.check, lost = 0 }: { ms?: number; lost?: number } = {},
): Step[] {
	return [
		[`${key}-launch`, T.launch, caption, launch(call, withoutLine), call.api],
		[
			`${key}-find`,
			ms,
			caption,
			compose(card(0, 'ok', found, 'found'), withoutLands(call.api, withoutLine, lost)),
			call.api,
		],
		[`${key}-identity`, ms, caption, card(1, 'ok', KNOWN, 'known'), call.api],
	];
}

/** A call that walks all five cards and lands on its API. */
function cleanPass(
	key: string,
	call: DemoCall,
	caption: string,
	[found, rules, vault, traced]: [found: Lines, rules: CardRow, vault: CardRow, record: Lines],
	result: string,
	withoutLine: string,
	ms = T.check,
): Step[] {
	const check = (i: number, [lines, verdict, tone]: CardRow) =>
		card(i, tone ?? 'ok', lines, verdict);
	return [
		...firstChecks(key, call, caption, found, withoutLine, { ms }),
		[`${key}-rules`, ms, caption, check(2, rules), call.api],
		[`${key}-vault`, ms, caption, check(3, vault), call.api],
		[
			`${key}-record`,
			T.record,
			`Recorded → call ${callTag(call)} appears in your Activity log at the same moment.`,
			record(key, call, traced, result),
			call.api,
		],
		[`${key}-land`, T.land, caption, land(call), call.api],
	];
}

const GMAIL = '2. Email the summary. The agent has no Gmail access yet, so it has to ask you.';
const DRIVE =
	'4. Clean up Drive. The agent decides "old files" means everything, and tries to delete them.';

const STEPS: Step[] = [
	['task', TASK_BEAT_MS, `You give ${DEMO_AGENT.name} this week's job.`, set({ intro: true })],
	[
		'register',
		4400,
		`First ${DEMO_AGENT.name} registers itself → the request lands in Notifications.`,
		set({
			intro: false,
			agentStatus: 'pending',
			pointer: 'approve',
			callout: {
				kind: 'needs',
				text: 'Needs you',
				from: 'agent',
				to: 'notifications',
			},
			bell: [
				{
					id: 'join',
					title: `${DEMO_AGENT.name} wants to join`,
					detail: 'Self-registered from the CLI',
					action: 'Approve',
				},
			],
		}),
	],
	[
		'approve',
		2800,
		'You approve it once. From now on the gateway knows exactly which agent is calling.',
		compose(
			set((s) => ({
				agentStatus: 'active',
				bell: [],
				pointer: 'none',
				withNote: 'known agent · approved by you',
				callout: {
					kind: 'recorded',
					text: 'Recorded',
					from: 'agent',
					to: 'activity',
				},
				stats: { ...s.stats, approvals: s.stats.approvals + 1 },
			})),
			logRow({
				id: 'approved',
				source: 'audit',
				tone: 'neutral',
				statusLabel: 'Recorded',
				title: `You approved ${DEMO_AGENT.name}`,
			}),
		),
	],
	...cleanPass(
		'github',
		DEMO_CALLS.githubIssues,
		'1. Read GitHub issues. Watch the call pass each card; the gateway adds the key, never the agent.',
		[
			['"this week\'s issues"', '→ issues.list'],
			[['GET github', 'rule: allow'], 'allowed'],
			[['+ ghp_••••', 'agent sees none'], 'key added'],
			['trace tr_01', '200 · 14 issues'],
		],
		'200',
		WITHOUT_OUTCOMES.github,
	),
	...firstChecks(
		'gmail',
		DEMO_CALLS.gmailSend,
		GMAIL,
		['"email the team"', '→ messages.send'],
		WITHOUT_OUTCOMES.gmail,
	),
	[
		'gmail-ask',
		5200,
		'Gmail is not connected, so the call waits for you → the request lands in Notifications.',
		compose(
			card(2, 'warn', ['waiting for you', '→ Notifications'], 'no access', {
				dotTone: 'warn',
				dotLabel: 'asking you',
				pointer: 'approve',
				pulse: 'gmail-wait',
				callout: {
					kind: 'needs',
					text: 'Needs you',
					from: 'gmail',
					to: 'notifications',
					tag: '2',
				},
			}),
			set((s) => ({
				plan: { ...s.plan, gmail: 'warn' },
				// The call that had to wait is recorded too.
				stats: { ...s.stats, traced: s.stats.traced + 1 },
				bell: [
					{
						id: 'connect-gmail',
						title: `${DEMO_AGENT.name} asks to connect Gmail`,
						detail: 'Scope: gmail.send',
						action: 'Approve and connect',
					},
				],
			})),
			logRow({
				id: 'gmail-wait',
				source: 'calls',
				tone: 'warn',
				statusLabel: 'Waiting',
				title: 'POST messages.send',
				api: 'gmail',
				detail: 'not connected',
				tag: '2',
			}),
		),
		'gmail',
	],
	[
		'gmail-connect',
		3000,
		'You press Approve → Gmail is connected, its key goes into the vault, and the call resumes.',
		compose(
			set((s) => ({
				bell: [],
				pointer: 'none',
				dotTone: 'default',
				dotLabel: '',
				connected: { ...s.connected, gmail: true },
				callout: {
					kind: 'recorded',
					text: 'Recorded',
					from: 'gmail',
					to: 'activity',
				},
				stats: { ...s.stats, approvals: s.stats.approvals + 1 },
			})),
			logRow({
				id: 'gmail-connected',
				source: 'audit',
				tone: 'neutral',
				statusLabel: 'Recorded',
				title: 'Gmail connected · key in the vault',
			}),
		),
		'gmail',
	],
	...cleanPass(
		'gmail-retry',
		DEMO_CALLS.gmailSend,
		'The call resumes and goes straight through.',
		[
			['', '→ messages.send'],
			[['POST gmail', 'rule: allow'], 'allowed'],
			[['+ ya29.••••', 'agent sees none'], 'key added'],
			['trace tr_02', '200 · sent'],
		],
		'200',
		WITHOUT_OUTCOMES.gmail,
		T.quick,
	),
	...cleanPass(
		'slack',
		DEMO_CALLS.slackPost,
		'3. Post to Slack. Your rule allows posting in #eng, so it just goes through.',
		[
			['"share in #eng"', '→ postMessage'],
			[['POST #eng', 'rule: allow'], 'allowed'],
			[['+ xoxb-••••', 'agent sees none'], 'key added'],
			['trace tr_03', '200 · posted'],
		],
		'200',
		WITHOUT_OUTCOMES.slack,
	),
	...firstChecks(
		'drive',
		DEMO_CALLS.driveDelete,
		DRIVE,
		['"clean up old files"', '→ files.delete'],
		DELETED,
		{ lost: UNGUARDED_FILES_DELETED },
	),
	[
		'drive-deny',
		4200,
		`Your rule denies deletes → the denied call lands in Activity, and nothing reaches Drive. Without a gateway, ${UNGUARDED_FILES_DELETED} files are gone.`,
		compose(
			card(2, 'fail', ['DELETE drive', 'rule: deny deletes'], 'denied', {
				dotTone: 'fail',
				dotLabel: '403 denied',
				pulse: 'drive-denied',
				callout: {
					kind: 'denied',
					text: 'Denied · logged',
					from: 'googledrive',
					to: 'activity',
					tag: '4',
				},
			}),
			set((s) => ({
				plan: { ...s.plan, googledrive: 'fail' },
				// Its row lands in Activity now, so it counts as traced now.
				stats: { ...s.stats, stopped: s.stats.stopped + 1, traced: s.stats.traced + 1 },
			})),
			logRow({
				id: 'drive-denied',
				source: 'calls',
				tone: 'fail',
				statusLabel: 'Denied',
				title: 'DELETE drive /files',
				api: 'googledrive',
				detail: 'denied',
				tag: '4',
			}),
		),
		'googledrive',
	],
	[
		'drive-record',
		3000,
		'The denied call is still traced, so you can see exactly what was tried.',
		compose(
			(s) => ({
				...s,
				cards: {
					...s.cards,
					record: {
						tone: 'ok',
						lines: ['trace tr_04', 'denied · logged'],
						verdict: 'traced',
					},
				},
			}),
			set({ pointer: 'pause' }),
		),
		'googledrive',
	],
	[
		'disable',
		4200,
		`You pause the agent, ${DEMO_AGENT.name}, in Monitor → it shows Paused in the lane and in Monitor, and serves no more calls.`,
		compose(
			set({
				agentStatus: 'disabled',
				pointer: 'none',
				call: null,
				dotAt: -1,
				dotTone: 'default',
				dotLabel: '',
				keyed: false,
				cards: IDLE_CARDS,
				apiHit: {},
				callout: {
					kind: 'paused',
					text: 'Agent paused',
					from: 'agent',
					to: 'agent',
				},
			}),
			logRow({
				id: 'disabled',
				source: 'audit',
				tone: 'neutral',
				statusLabel: 'Recorded',
				title: `You paused ${DEMO_AGENT.name}`,
			}),
		),
	],
	[
		'end',
		5500,
		'One agent, four APIs, zero keys in the agent. You approved what it joined and connected, your rules stopped the bad call, and every call left a trace.',
		set({ settled: true }),
	],
];

const WHY_BEATS: readonly WhyBeat[] = STEPS.map(beat);

export const WHY_SCRIPT: Script<WhyState> = { initial: WHY_INITIAL, beats: WHY_BEATS };

/** Each plan step's API and the first / last beat it spans (from the beats' `api` tags). */
export const PLAN_SPANS: readonly { api: StoryApiId; first: number; last: number }[] =
	STORY_APIS.map((api) => {
		const idx = WHY_BEATS.flatMap((b, i) => (b.api === api ? [i] : []));
		return { api, first: idx[0] ?? 0, last: idx[idx.length - 1] ?? 0 };
	});

/** Where the loop picks up again: past the typed task. */
export const WHY_LOOP_FROM = 1;
