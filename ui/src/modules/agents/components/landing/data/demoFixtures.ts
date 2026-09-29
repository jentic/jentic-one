/**
 * The Agents landing's demo world — the ONLY fake data the landing renders.
 * One agent, a handful of well-known APIs and the operations its story calls.
 * Nothing here is fetched, persisted or shared with MSW.
 */
import type { AgentMarkSlug, VendorMarkSlug } from '@/shared/ui';

export const DEMO_AGENT = { name: 'research-bot' } as const;

/** The APIs in the story (the AI agent marks are not APIs). */
export type DemoApiId = Exclude<VendorMarkSlug, AgentMarkSlug>;

export interface DemoApi {
	id: DemoApiId;
	name: string;
}

export const DEMO_APIS: Record<DemoApiId, DemoApi> = {
	github: { id: 'github', name: 'GitHub' },
	gmail: { id: 'gmail', name: 'Gmail' },
	slack: { id: 'slack', name: 'Slack' },
	googledrive: { id: 'googledrive', name: 'Google Drive' },
	stripe: { id: 'stripe', name: 'Stripe' },
	linear: { id: 'linear', name: 'Linear' },
	notion: { id: 'notion', name: 'Notion' },
};

/** The four APIs Act 1's job touches, in the order it touches them. */
export const STORY_APIS = [
	'github',
	'gmail',
	'slack',
	'googledrive',
] as const satisfies readonly DemoApiId[];

export type StoryApiId = (typeof STORY_APIS)[number];

/** The agent's .env without a gateway: every story API's key, masked. */
export const ENV_SECRETS: readonly { api: StoryApiId; line: string }[] = [
	{ api: 'github', line: 'GITHUB_TOKEN=ghp_••••' },
	{ api: 'gmail', line: 'GMAIL_OAUTH=ya29.••••' },
	{ api: 'slack', line: 'SLACK_BOT_TOKEN=xoxb-••••' },
	{ api: 'googledrive', line: 'GDRIVE_KEY=AIza••••' },
];

export interface DemoCall {
	api: StoryApiId;
	method: 'GET' | 'POST' | 'DELETE';
	/** Operation id as the gateway names it. */
	op: string;
}

export const DEMO_CALLS = {
	githubIssues: { api: 'github', method: 'GET', op: 'issues.list' },
	gmailSend: { api: 'gmail', method: 'POST', op: 'messages.send' },
	slackPost: { api: 'slack', method: 'POST', op: 'chat.postMessage' },
	driveDelete: { api: 'googledrive', method: 'DELETE', op: 'files.delete' },
} as const satisfies Record<string, DemoCall>;

/** The files the WITHOUT lane loses to the unguarded clean-up. */
export const UNGUARDED_FILES_DELETED = 248;
