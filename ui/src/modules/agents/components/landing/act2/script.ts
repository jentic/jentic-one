/**
 * Act 2's script — a self-playing tour of the six real steps. Each step is a
 * handful of frames; a frame names where the scripted cursor rests (a
 * `data-cursor` target in the miniature) and whether it clicks. The stage draws
 * a miniature of the real screen for the current (step, frame). Data only.
 */
import type { Beat, Script } from '@/modules/agents/components/landing/timeline';

export interface SetupFrame {
	ms: number;
	/** The `data-cursor` target the pointer glides to; omitted → it rests. */
	cursor?: string;
	/** The pointer clicks when it arrives. */
	press?: boolean;
}

const f = (ms: number, cursor?: string, press = false): SetupFrame => ({ ms, cursor, press });

export const SETUP_STEPS = [
	{
		id: 'create',
		title: 'Create the agent',
		url: '/agents',
		you: 'Click New agent and give it a name. Or run jentic register and the agent signs itself up.',
		jentic: 'Gives the agent its own identity. Self-registered agents wait for your approval.',
		frames: [
			f(900, 'new-agent'),
			f(700, 'new-agent', true),
			f(1500, 'name'),
			f(900, 'create', true),
			f(1300, 'approve'),
			f(1400, 'approve', true),
		],
	},
	{
		id: 'apis',
		title: 'Choose its APIs',
		url: '/agents?agent=research-bot&add-apis=1',
		you: 'Search the catalog and pick the APIs this agent may use. Or import your own OpenAPI spec.',
		jentic: 'Knows thousands of APIs and their operations, so the agent can search them in plain words.',
		frames: [
			f(1300, 'search'),
			f(900, 'api-slack', true),
			f(900, 'api-github', true),
			f(900, 'api-stripe', true),
			f(1400, 'continue', true),
		],
	},
	{
		id: 'keys',
		title: 'Add the keys',
		url: '/agents?agent=research-bot&setup=keys',
		you: 'For each API: sign in with OAuth, reuse a key you already added, or paste a new one.',
		jentic: 'Keeps every key in its vault and adds it to each call. The agent never sees a key.',
		frames: [
			f(900, 'oauth-slack', true),
			f(900, 'reuse-github', true),
			f(1400, 'key-stripe'),
			f(900, 'save-stripe', true),
			f(1500),
		],
	},
	{
		id: 'rules',
		title: 'Set the rules',
		url: '/agents?agent=research-bot&tab=permissions',
		you: 'Every new API starts with no access. Choose what the agent may do, then test a call.',
		jentic: 'Checks every call against your rules: allow it or deny it. Denied calls never reach the API.',
		frames: [
			f(1000, 'rule-read'),
			f(800, 'rule-read', true),
			f(900, 'rule-write', true),
			f(1700, 'tester'),
			f(1900, 'tester'),
		],
	},
	{
		id: 'connect',
		title: 'Connect your agent',
		url: '/agents?agent=research-bot&tab=mcp',
		you: 'Paste the MCP link into Claude or Cursor, or use the CLI. Then just ask it to do the job.',
		jentic: 'Finds the right APIs, adds the keys, applies your rules, and records every call in Monitor.',
		frames: [
			f(900, 'copy'),
			f(900, 'copy', true),
			f(1100),
			f(2400),
			f(1300),
			f(1900),
			f(1900),
			f(2200),
			f(2400),
		],
	},
	{
		id: 'govern',
		title: 'Govern from Monitor',
		url: '/monitor',
		you: 'Approve new agents and connection requests, open any trace, spot denied calls, pause or revoke an agent.',
		jentic: 'Records every call and event: who, what, which rule, and the result.',
		frames: [
			f(900),
			f(900),
			f(1200, 'approve'),
			f(1100, 'approve', true),
			f(900, 'pause'),
			f(2200, 'pause', true),
		],
	},
] as const satisfies ReadonlyArray<{
	id: string;
	title: string;
	url: string;
	you: string;
	jentic: string;
	frames: readonly SetupFrame[];
}>;

export interface SetupState {
	step: number;
	frame: number;
}

export const SETUP_SCRIPT: Script<SetupState> = {
	initial: { step: 0, frame: 0 },
	beats: SETUP_STEPS.flatMap((step, stepIndex): Beat<SetupState>[] =>
		step.frames.map((frame, i) => ({
			id: `${step.id}-${i}`,
			durationMs: frame.ms,
			caption: `Step ${stepIndex + 1} of ${SETUP_STEPS.length}: ${step.title}. ${step.you}`,
			apply: () => ({ step: stepIndex, frame: i }),
		})),
	),
};

/** The frame metadata for a (step, frame). */
export function setupFrame(step: number, frame: number): SetupFrame {
	return SETUP_STEPS[step].frames[frame] ?? { ms: 0 };
}

/** The beat index a step starts at. */
export function stepStartIndex(stepIndex: number): number {
	return SETUP_STEPS.slice(0, stepIndex).reduce((n, s) => n + s.frames.length, 0);
}

/** The beat id a step starts at (seek target for the step list). */
export function stepStartBeat(stepIndex: number): string {
	return `${SETUP_STEPS[stepIndex].id}-0`;
}
