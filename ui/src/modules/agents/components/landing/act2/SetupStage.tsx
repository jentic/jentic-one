/**
 * Act 2's miniatures — small, frame-driven replicas of the real screens, drawn
 * with the kit. They are decoration for the caption (each renders `inert` +
 * `aria-hidden`); the step's CTA under the frame is the real action. Elements
 * the scripted cursor visits carry `data-cursor`.
 */
import type { ReactNode } from 'react';
import { Bot, KeyRound, Lock, Pause, Plus, Search, ShieldCheck } from 'lucide-react';
import { StatusGlyph, VendorMark } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import {
	DEMO_AGENT,
	DEMO_APIS,
	type DemoApiId,
} from '@/modules/agents/components/landing/data/demoFixtures';
import { Caret, MiniButton } from '@/modules/agents/components/landing/act2/StageKit';
import { ACTIVITY_SOURCE_OPTIONS } from '@/modules/agents/components/landing/operator';
import { useTyped } from '@/modules/agents/components/landing/useTyped';

export interface MiniProps {
	frame: number;
	reduced: boolean;
	/** The `data-cursor` target being clicked in this frame, if any. */
	pressing?: string;
}

const dim = 'text-muted-foreground';
const field =
	'border-border bg-card min-h-8 rounded-md border px-2.5 py-1.5 font-mono text-xs text-foreground';

function Screen({ children, className }: { children: ReactNode; className?: string }) {
	return (
		<div inert aria-hidden="true" className={cn('relative h-full text-xs', className)}>
			{children}
		</div>
	);
}

function Top({ title, children }: { title: ReactNode; children?: ReactNode }) {
	return (
		<div className="mb-3 flex items-center gap-2">
			<span className="font-heading text-foreground text-sm font-semibold">{title}</span>
			<span className="ml-auto flex items-center gap-2">{children}</span>
		</div>
	);
}

/* ------------------------------------------------------------------ create */

/** Create's frames: 0-1 the fleet · 2 the sheet, name typed · 3 create · 4 a self-registered agent waits · 5 approved. */
const CREATE = { naming: 2, created: 3, waiting: 4, approved: 5 } as const;

export function CreateStage({ frame, reduced, pressing }: MiniProps) {
	const sheet = frame === CREATE.naming || frame === CREATE.created;
	const name = useTyped(DEMO_AGENT.name, frame === CREATE.naming, reduced, 70);
	return (
		<Screen className="overflow-hidden">
			<Top title="Agents">
				<MiniButton cursor="new-agent" pressed={pressing === 'new-agent'}>
					<Plus className="h-3 w-3" />
					New agent
				</MiniButton>
			</Top>
			<div className="space-y-2">
				{frame >= CREATE.waiting && (
					<div className="border-border bg-card animate-rise flex items-center gap-2 rounded-lg border px-3 py-2">
						<Bot className="text-primary h-4 w-4" />
						<span className="font-mono">{DEMO_AGENT.name}</span>
						<span className="text-success ml-auto text-[11px]">Active</span>
					</div>
				)}
				{frame >= CREATE.waiting && (
					<div
						className={cn(
							'animate-rise flex items-center gap-2 rounded-lg border px-3 py-2 transition-colors duration-300',
							frame >= CREATE.approved
								? 'border-success/50 bg-success/[0.05]'
								: 'border-warning/50 bg-warning/[0.05]',
						)}
					>
						<StatusGlyph tone={frame >= CREATE.approved ? 'ok' : 'warn'} />
						<span className="font-mono">cursor-agent</span>
						<span className={dim}>registered itself from the CLI</span>
						<span className="ml-auto">
							{frame >= CREATE.approved ? (
								<MiniButton variant="done">Approved</MiniButton>
							) : (
								<MiniButton cursor="approve" pressed={pressing === 'approve'}>
									Approve
								</MiniButton>
							)}
						</span>
					</div>
				)}
				{frame < CREATE.waiting && (
					<p className={cn(dim, 'pt-10 text-center')}>No agents yet.</p>
				)}
			</div>
			<div
				className={cn(
					'border-primary/60 bg-card absolute inset-y-0 right-0 w-64 space-y-2 border-l p-4 transition-transform duration-500 ease-(--ease-out-soft)',
					sheet ? 'translate-x-0' : 'translate-x-[110%]',
				)}
			>
				<p className="font-heading text-sm font-semibold">New agent</p>
				<p className={cn(dim, 'text-[11px]')}>Name</p>
				<div className={field} data-cursor="name">
					{frame >= CREATE.created ? DEMO_AGENT.name : name}
					<Caret on={frame === CREATE.naming} />
				</div>
				<p className={cn(dim, 'text-[11px]')}>Description</p>
				<div className={cn(field, dim)}>Weekly reports for the team</div>
				<MiniButton cursor="create" pressed={pressing === 'create'} className="mt-1">
					Create and add APIs
				</MiniButton>
			</div>
		</Screen>
	);
}

/* -------------------------------------------------------------------- apis */

const CATALOG: Array<{ api: DemoApiId; ops: number }> = [
	{ api: 'slack', ops: 220 },
	{ api: 'github', ops: 900 },
	{ api: 'stripe', ops: 480 },
	{ api: 'linear', ops: 60 },
	{ api: 'gmail', ops: 75 },
	{ api: 'notion', ops: 40 },
];
const PICK_ORDER: DemoApiId[] = ['slack', 'github', 'stripe'];

/**
 * APIs' frames: 0 "slack" typed · 1-3 one pick each, in `PICK_ORDER` · 4
 * continue. The search clears once the first pick is made.
 */
const APIS = { typing: 0, firstPick: 1 } as const;

export function ApisStage({ frame, reduced, pressing }: MiniProps) {
	const typed = useTyped('slack', frame === APIS.typing, reduced, 110);
	const query = frame === APIS.typing ? typed : frame === APIS.firstPick ? 'slack' : '';
	const picked = PICK_ORDER.slice(0, Math.max(0, frame - APIS.firstPick + 1));
	return (
		<Screen>
			<Top title={`Add APIs to ${DEMO_AGENT.name}`} />
			<div className={cn(field, 'mb-3 flex items-center gap-2')} data-cursor="search">
				<Search className={cn(dim, 'h-3.5 w-3.5')} />
				{query}
				<Caret on={frame === APIS.typing} />
			</div>
			<div className="mb-3 grid grid-cols-3 gap-2">
				{CATALOG.map(({ api, ops }) => {
					const on = picked.includes(api);
					const hidden =
						query !== '' && !DEMO_APIS[api].name.toLowerCase().startsWith(query);
					return (
						<div
							key={api}
							data-cursor={`api-${api}`}
							className={cn(
								'flex items-center gap-2 rounded-lg border p-2.5 transition-[opacity,border-color,background-color,transform] duration-300',
								on ? 'border-success bg-success/[0.08]' : 'border-border bg-card',
								// Filtered out by the search: the card recedes (border, logo)
								// but its words stay readable — muted text, not faded.
								hidden && 'border-border/40 bg-transparent',
								pressing === `api-${api}` && 'scale-95',
							)}
						>
							<span
								className={cn(
									'transition-opacity duration-300',
									hidden && 'opacity-30',
								)}
							>
								<VendorMark slug={api} size="md" />
							</span>
							<span className="min-w-0 flex-1">
								<span
									className={cn(
										'block font-semibold transition-colors duration-300',
										hidden ? 'text-muted-foreground' : 'text-foreground',
									)}
								>
									{DEMO_APIS[api].name}
								</span>
								<span className={cn(dim, 'block text-[11px]')}>{ops} ops</span>
							</span>
							{on && <StatusGlyph tone="ok" />}
						</div>
					);
				})}
			</div>
			<MiniButton cursor="continue" pressed={pressing === 'continue'}>
				Continue with {picked.length} API{picked.length === 1 ? '' : 's'}
			</MiniButton>
		</Screen>
	);
}

/* -------------------------------------------------------------------- keys */

/** Keys' frames: 0 Slack OAuth · 1 GitHub reuse · 2 the Stripe key typed · 3 save · 4 all in the vault. */
const KEYS = { slackDone: 1, githubDone: 2, typingKey: 2, keyEntered: 3, stripeDone: 4 } as const;

export function KeysStage({ frame, reduced, pressing }: MiniProps) {
	const key = useTyped('sk_live_••••••••', frame === KEYS.typingKey, reduced, 60);
	const rows: Array<{ api: DemoApiId; done: boolean; doneLabel: string; action: ReactNode }> = [
		{
			api: 'slack',
			done: frame >= KEYS.slackDone,
			doneLabel: 'connected',
			action: (
				<MiniButton cursor="oauth-slack" pressed={pressing === 'oauth-slack'}>
					Connect with Slack
				</MiniButton>
			),
		},
		{
			api: 'github',
			done: frame >= KEYS.githubDone,
			doneLabel: 'reused',
			action: (
				<MiniButton
					variant="ghost"
					cursor="reuse-github"
					pressed={pressing === 'reuse-github'}
				>
					Use “GitHub (team)”
				</MiniButton>
			),
		},
		{
			api: 'stripe',
			done: frame >= KEYS.stripeDone,
			doneLabel: 'in the vault',
			action: (
				<span className="flex flex-1 items-center gap-2">
					<span className={cn(field, 'flex-1')} data-cursor="key-stripe">
						{frame >= KEYS.keyEntered
							? 'sk_live_••••••••'
							: frame === KEYS.typingKey
								? key
								: ''}
						<Caret on={frame === KEYS.typingKey} />
					</span>
					<MiniButton cursor="save-stripe" pressed={pressing === 'save-stripe'}>
						Save
					</MiniButton>
				</span>
			),
		},
	];
	const doneCount = rows.filter((r) => r.done).length;
	return (
		<Screen>
			<Top title="Set up 3 APIs">
				<span className={dim}>{doneCount} of 3</span>
			</Top>
			<div className="space-y-2">
				{rows.map((r) => (
					<div
						key={r.api}
						className={cn(
							'bg-card flex min-h-12 items-center gap-3 rounded-lg border px-3 py-2 transition-colors duration-300',
							r.done ? 'border-success/50' : 'border-border',
						)}
					>
						<VendorMark slug={r.api} size="md" />
						<span className="text-foreground w-14 font-semibold">
							{DEMO_APIS[r.api].name}
						</span>
						{r.done ? (
							<span className="text-success animate-rise ml-auto inline-flex items-center gap-1.5">
								<Lock className="h-3.5 w-3.5" />
								{r.doneLabel}
							</span>
						) : (
							<span className="ml-auto flex flex-1 justify-end">{r.action}</span>
						)}
					</div>
				))}
			</div>
			<p className={cn(dim, 'mt-3 flex items-center gap-1.5 text-[11px]')}>
				<KeyRound className="h-3.5 w-3.5" />
				Keys stay in the vault. {DEMO_AGENT.name} never sees one.
			</p>
		</Screen>
	);
}

/* ------------------------------------------------------------------- rules */

function Seg({ allow, cursor }: { allow: boolean; cursor?: string }) {
	return (
		<span
			className="border-border inline-flex overflow-hidden rounded-md border"
			data-cursor={cursor}
		>
			<span
				className={cn(
					'px-2.5 py-1 text-[11px] transition-colors duration-200',
					allow ? 'bg-success text-background font-semibold' : dim,
				)}
			>
				Allow
			</span>
			<span
				className={cn(
					'px-2.5 py-1 text-[11px] transition-colors duration-200',
					!allow ? 'bg-danger/80 text-background font-semibold' : dim,
				)}
			>
				Deny
			</span>
		</span>
	);
}

/** Rules' frames: 0 all deny · 1 read → allow · 2 writes stay denied · 3 test a delete · 4 test a read. */
const RULES = { readAllowed: 1, testDelete: 3, testRead: 4 } as const;
const DELETE_TEST = 'DELETE /customers';
const READ_TEST = 'GET /charges';

export function RulesStage({ frame, reduced }: MiniProps) {
	const first = useTyped(DELETE_TEST, frame === RULES.testDelete, reduced, 60);
	const second = useTyped(READ_TEST, frame === RULES.testRead, reduced, 70);
	const test = frame === RULES.testDelete ? first : frame >= RULES.testRead ? second : '';
	const verdict =
		frame === RULES.testDelete && first.length === DELETE_TEST.length
			? 'blocked'
			: frame >= RULES.testRead && second.length === READ_TEST.length
				? 'allowed'
				: null;
	return (
		<Screen>
			<Top
				title={
					<span className="inline-flex items-center gap-2">
						<VendorMark slug="stripe" size="sm" />
						Permissions · Stripe
					</span>
				}
			>
				<span className={dim}>starts at: no access</span>
			</Top>
			<div className="divide-border border-border bg-card divide-y rounded-lg border">
				{[
					{ label: 'Read (GET)', allow: frame >= RULES.readAllowed, cursor: 'rule-read' },
					{ label: 'Create / update', allow: false, cursor: 'rule-write' },
					{ label: 'Delete', allow: false },
				].map((r) => (
					<div key={r.label} className="flex items-center justify-between px-3 py-2.5">
						<span className="text-foreground">{r.label}</span>
						<Seg allow={r.allow} cursor={r.cursor} />
					</div>
				))}
			</div>
			<div
				className="border-border mt-3 flex items-center justify-between rounded-lg border border-dashed px-3 py-2.5"
				data-cursor="tester"
			>
				<span className="font-mono">
					<span className={dim}>Test: </span>
					{test}
					<Caret on={frame >= RULES.testDelete && verdict === null} />
				</span>
				{verdict && (
					<span
						key={verdict}
						className={cn(
							'animate-rise inline-flex items-center gap-1 font-semibold',
							verdict === 'allowed' ? 'text-success' : 'text-danger',
						)}
					>
						<StatusGlyph tone={verdict === 'allowed' ? 'ok' : 'fail'} />
						{verdict}
					</span>
				)}
			</div>
			<p className={cn(dim, 'mt-3 flex items-center gap-1.5 text-[11px]')}>
				<ShieldCheck className="h-3.5 w-3.5" />
				Denied calls never reach Stripe.
			</p>
		</Screen>
	);
}

/* ------------------------------------------------------------------ govern */

interface FeedRow {
	id: string;
	tone: 'ok' | 'fail';
	api: DemoApiId;
	text: string;
	detail: string;
}

const FEED: Array<FeedRow & { at: number }> = [
	{ id: 'gh', at: 0, tone: 'ok', api: 'github', text: 'GET /issues', detail: 'allowed · 212ms' },
	{ id: 'ch', at: 1, tone: 'ok', api: 'stripe', text: 'GET /charges', detail: 'allowed' },
	{ id: 'dl', at: 2, tone: 'fail', api: 'stripe', text: 'DELETE /customers', detail: 'denied' },
	{ id: 'gm', at: 3, tone: 'ok', api: 'gmail', text: 'POST /messages/send', detail: '200' },
];

/** Govern's frames: 0-3 one feed row each (`FEED[].at`) · 3 approve Gmail · 4-5 pause the agent. */
const GOVERN = { approved: 3, paused: 5 } as const;

export function GovernStage({ frame, pressing }: MiniProps) {
	const approved = frame >= GOVERN.approved;
	const paused = frame >= GOVERN.paused;
	const rows = FEED.filter((r) => r.at <= frame).reverse();
	return (
		<Screen>
			<Top title="Monitor">
				<span className="text-success inline-flex items-center gap-1 text-[11px]">
					<span className="bg-success h-1.5 w-1.5 animate-pulse rounded-full" />
					live
				</span>
				<MiniButton
					variant={paused ? 'done' : 'ghost'}
					cursor="pause"
					pressed={pressing === 'pause'}
				>
					<Pause className="h-3 w-3" />
					{paused ? 'Paused' : `Pause ${DEMO_AGENT.name}`}
				</MiniButton>
			</Top>
			<div className="grid h-[calc(100%-2.75rem)] gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
				<div className="border-border bg-card rounded-lg border p-3">
					<p className="font-heading text-foreground mb-2 text-[13px] font-semibold">
						Notifications
					</p>
					<div
						className={cn(
							'flex items-start gap-2 rounded-lg border p-2 transition-colors duration-300',
							approved ? 'border-success/50' : 'border-warning/50',
						)}
					>
						<StatusGlyph tone={approved ? 'ok' : 'warn'} />
						<span className="min-w-0 flex-1">
							<span className="text-foreground block">
								{approved
									? 'Gmail connected'
									: `${DEMO_AGENT.name} asks to connect Gmail`}
							</span>
							<span className={cn(dim, 'block text-[11px]')}>
								{approved
									? 'approved by you · key in the vault'
									: 'scope: gmail.send'}
							</span>
						</span>
						{!approved && (
							<MiniButton cursor="approve" pressed={pressing === 'approve'}>
								Approve
							</MiniButton>
						)}
					</div>
				</div>
				<div className="border-border bg-card overflow-hidden rounded-lg border p-3">
					<div className="mb-2 flex items-center gap-2">
						<p className="font-heading text-foreground text-[13px] font-semibold">
							Activity
						</p>
						<span className="ml-auto flex gap-1 text-[10px]">
							{ACTIVITY_SOURCE_OPTIONS.map(({ value, label }, i) => (
								<span
									key={value}
									className={cn(
										'rounded-full border px-2 py-0.5',
										i === 0
											? 'border-primary text-foreground'
											: 'border-border text-muted-foreground',
									)}
								>
									{label}
								</span>
							))}
						</span>
					</div>
					<ul className="space-y-1.5">
						{paused && (
							<li className="animate-arrive border-border flex items-center gap-2 rounded-md border px-2 py-1.5">
								<StatusGlyph tone="neutral" />
								<span className="text-foreground flex-1">
									You paused {DEMO_AGENT.name}
								</span>
							</li>
						)}
						{rows.map((r) => (
							<li
								key={r.id}
								className={cn(
									'animate-arrive flex items-center gap-2 rounded-md border px-2 py-1.5',
									r.tone === 'fail' ? 'border-danger/40' : 'border-border',
								)}
							>
								<StatusGlyph tone={r.tone} />
								<VendorMark slug={r.api} size="xs" />
								<span className="text-foreground min-w-0 flex-1 truncate font-mono text-[11px]">
									{r.text}
								</span>
								<span
									className={cn(
										'font-mono text-[11px]',
										r.tone === 'fail' ? 'text-danger' : dim,
									)}
								>
									{r.detail}
								</span>
							</li>
						))}
					</ul>
				</div>
			</div>
		</Screen>
	);
}
