/**
 * Act 2's step-5 miniature — connecting the agent. The MCP snippet is copied,
 * the first call arrives, and the screen splits: on the left the agent at work
 * in Chat (an MCP client) or Terminal (the jentic CLI) — the viewer picks with a
 * real toggle — and on the right a mini Activity log where one row lands per
 * call. Everything but the toggle is decoration (`inert` + `aria-hidden`).
 */
import type { ReactNode } from 'react';
import { KeyRound, Loader2 } from 'lucide-react';
import { McpIcon, StatusGlyph, VendorMark } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { DEMO_AGENT, type DemoApiId } from '@/modules/agents/components/landing/data/demoFixtures';
import { Caret, MiniButton } from '@/modules/agents/components/landing/act2/StageKit';
import { useTyped } from '@/modules/agents/components/landing/useTyped';
import type { MiniProps } from '@/modules/agents/components/landing/act2/SetupStage';

export type ConnectMode = 'chat' | 'terminal';

/*
 * The step's frames (see `SETUP_STEPS`): 0 snippet · 1 copied · 2 connected ·
 * 3 the prompt (the split view opens) · 4-6 one tool call each · 7 the answer ·
 * 8 the summary.
 */
const COPIED_FRAME = 1;
const CONNECTED_FRAME = 2;
/** The frame the split view starts playing at (seek target when the mode flips). */
export const CONNECT_SPLIT_FRAME = 3;
/** Tool call `k` has returned from frame `FIRST_RESULT_FRAME + k`. */
const FIRST_RESULT_FRAME = 4;
const ANSWER_FRAME = 7;
const SUMMARY_FRAME = 8;

const PROMPT = "Summarise this week's GitHub issues and post the summary to #eng on Slack";
const ANSWER =
	'Done. 14 open issues: 3 bugs, 2 need review, 9 in progress. Summary posted to #eng.';

interface ToolStep {
	chat: ReactNode;
	command: string;
	result: string;
	row: { api: DemoApiId | 'jentic'; op: string; status: string; trace: string };
	keyAdded?: boolean;
}

/** Real CLI syntax: `jentic search <query>`, `jentic execute <operation-id> --path/--query/-d`. */
const TOOL_STEPS: ToolStep[] = [
	{
		chat: (
			<>
				jentic.search <b className="text-primary font-medium">"github issues"</b>
			</>
		),
		command: 'jentic search "github issues"',
		result: '3 operations · issues/list-for-repo · issues/get · search/issues-and-pull-requests',
		row: { api: 'jentic', op: 'search', status: '200 · 38ms', trace: 'tr_8f2a' },
	},
	{
		chat: (
			<>
				jentic.execute <b className="text-primary font-medium">github</b> issues.list
			</>
		),
		command:
			'jentic execute issues/list-for-repo --path owner=acme --path repo=app --query state=open',
		result: '200 · 14 issues',
		row: { api: 'github', op: 'issues.list', status: '200 · 212ms', trace: 'tr_8f2b' },
		keyAdded: true,
	},
	{
		chat: (
			<>
				jentic.execute <b className="text-primary font-medium">slack</b> chat.postMessage
			</>
		),
		command:
			'jentic execute chat.postMessage -d \'{"channel":"#eng","text":"14 open issues: 3 bugs, 2 need review"}\'',
		result: '200 · posted to #eng',
		row: { api: 'slack', op: 'chat.postMessage', status: '200 · 164ms', trace: 'tr_8f2c' },
	},
];

const dim = 'text-muted-foreground';

function ChatPane({ frame, reduced }: { frame: number; reduced: boolean }) {
	const prompt = useTyped(PROMPT, frame === CONNECT_SPLIT_FRAME, reduced, 22);
	const answer = useTyped(ANSWER, frame === ANSWER_FRAME, reduced, 18);
	return (
		<>
			{frame >= CONNECT_SPLIT_FRAME && (
				<p className="bg-muted border-border ml-auto max-w-[90%] rounded-lg rounded-br-sm border px-2.5 py-1.5">
					{frame === CONNECT_SPLIT_FRAME ? prompt : PROMPT}
				</p>
			)}
			{TOOL_STEPS.map((s, k) =>
				frame >= FIRST_RESULT_FRAME + k ? (
					<div key={k} className="animate-rise space-y-0.5">
						<p className="border-border bg-background flex items-center gap-2 rounded-md border px-2 py-1 font-mono text-[10.5px] whitespace-nowrap">
							<span className="min-w-0 truncate">{s.chat}</span>
							<span className="text-success ml-auto shrink-0">
								{s.result.split(' · ').slice(0, 2).join(' · ')}
							</span>
						</p>
						{s.keyAdded && (
							<p className="text-success flex items-center gap-1 pl-2 text-[10.5px]">
								<KeyRound className="h-3 w-3" />
								key added by Jentic · the agent never saw it
							</p>
						)}
					</div>
				) : frame === CONNECT_SPLIT_FRAME + k && frame >= FIRST_RESULT_FRAME ? (
					// Called as the previous call's result lands.
					<p
						key={k}
						className="border-border bg-background flex items-center gap-2 rounded-md border px-2 py-1 font-mono text-[10.5px]"
					>
						<span className="min-w-0 truncate">{s.chat}</span>
						<Loader2 className="text-warning ml-auto h-3 w-3 animate-spin" />
					</p>
				) : null,
			)}
			{frame >= ANSWER_FRAME && (
				<p className="animate-rise text-foreground">
					{frame === ANSWER_FRAME ? answer : ANSWER}
				</p>
			)}
			{frame >= ANSWER_FRAME && (
				<div className="border-border border-l-success bg-background animate-rise rounded-md border border-l-[3px] px-2 py-1 text-[10.5px]">
					<b className="font-heading">#eng</b> · {DEMO_AGENT.name}{' '}
					<span className={dim}>now</span>
					<br />
					This week on GitHub: 14 open · 3 bugs · 2 need review
				</div>
			)}
		</>
	);
}

function TermCommand({
	text,
	typing,
	reduced,
}: {
	text: string;
	typing: boolean;
	reduced: boolean;
}) {
	const typed = useTyped(text, typing, reduced, 14);
	return (
		<p className="text-foreground mt-1 break-all whitespace-pre-wrap">
			<span className="text-success font-bold">❯ </span>
			{typing ? typed : text}
			<Caret on={typing} tone="success" />
		</p>
	);
}

function TerminalPane({ frame, reduced }: { frame: number; reduced: boolean }) {
	// Command k is typed on the frame before its result lands.
	return (
		<>
			{frame >= CONNECT_SPLIT_FRAME && (
				<p className={dim}># {DEMO_AGENT.name} · token from jentic register</p>
			)}
			{TOOL_STEPS.map((s, k) =>
				frame >= CONNECT_SPLIT_FRAME + k ? (
					<div key={k}>
						<TermCommand
							text={s.command}
							typing={frame === CONNECT_SPLIT_FRAME + k}
							reduced={reduced}
						/>
						{frame >= FIRST_RESULT_FRAME + k && (
							<p className={cn(dim, 'animate-rise pl-3.5')}>
								<span className="text-success">✓ </span>
								{s.result}
								{s.keyAdded && (
									<span className="text-success"> · key added by Jentic</span>
								)}
							</p>
						)}
					</div>
				) : null,
			)}
			{frame >= ANSWER_FRAME && (
				<p className="text-foreground mt-1">
					<span className="text-success font-bold">❯ </span>
					<Caret tone="success" className="ml-0" />
				</p>
			)}
		</>
	);
}

export function ConnectStage({
	frame,
	reduced,
	pressing,
	mode,
	toggle,
}: MiniProps & { mode: ConnectMode; toggle: ReactNode }) {
	const connected = frame >= CONNECTED_FRAME;
	const rows = TOOL_STEPS.filter((_, k) => frame >= FIRST_RESULT_FRAME + k).map((s) => s.row);
	return (
		<div className="flex h-full flex-col gap-2 text-xs">
			<div inert aria-hidden="true" className="flex items-center gap-2">
				<span className="font-heading text-foreground text-sm font-semibold">
					{DEMO_AGENT.name} · Connect
				</span>
				<span
					className={cn(
						'text-success border-success/60 ml-auto rounded-full border px-2 py-0.5 text-[11px] transition-opacity duration-300',
						connected ? 'opacity-100' : 'opacity-0',
					)}
				>
					Connected
				</span>
			</div>
			{frame < CONNECT_SPLIT_FRAME && (
				<div inert aria-hidden="true" className="animate-rise space-y-2">
					<div className="flex gap-1.5 text-[11px]">
						{['MCP', 'API key', 'CLI'].map((t, i) => (
							<span
								key={t}
								className={cn(
									'rounded-full border px-2.5 py-0.5',
									i === 0
										? 'border-primary text-foreground'
										: 'border-border text-muted-foreground',
								)}
							>
								{t}
							</span>
						))}
					</div>
					<div className="border-border bg-background flex items-center gap-2 rounded-lg border px-3 py-2.5 font-mono">
						<span className="min-w-0 flex-1 truncate">
							claude mcp add jentic --url https://you.jentic.one/mcp
						</span>
						<MiniButton cursor="copy" pressed={pressing === 'copy'}>
							{frame >= COPIED_FRAME ? 'Copied' : 'Copy'}
						</MiniButton>
					</div>
					<div
						className={cn(
							'rounded-lg border px-3 py-2.5 transition-colors duration-300',
							connected
								? 'border-success/60 text-success'
								: 'border-border text-muted-foreground',
						)}
					>
						{connected
							? `Connected · ${DEMO_AGENT.name} is live`
							: 'Waiting for the first call…'}
					</div>
				</div>
			)}
			<div className="grid min-h-0 flex-1 gap-2 sm:grid-cols-[minmax(0,58fr)_minmax(0,42fr)]">
				<div
					className={cn(
						'border-border flex min-h-0 flex-col overflow-hidden rounded-lg border',
						mode === 'terminal' ? 'bg-background' : 'bg-card',
					)}
				>
					<div className="border-border flex items-center gap-2 border-b px-2 py-1">
						{toggle}
						<span
							inert
							aria-hidden="true"
							className={cn(dim, 'ml-auto flex items-center gap-1 text-[10.5px]')}
						>
							{mode === 'chat' ? (
								<>
									<McpIcon className="h-3 w-3" />
									connected to Jentic
								</>
							) : (
								<span className="font-mono">zsh · jentic CLI</span>
							)}
						</span>
					</div>
					<div
						inert
						aria-hidden="true"
						className={cn(
							'flex min-h-0 flex-1 flex-col justify-end gap-1.5 overflow-hidden p-2 leading-snug',
							mode === 'terminal' && 'gap-0.5 font-mono text-[10.5px]',
						)}
					>
						{frame < CONNECT_SPLIT_FRAME && (
							<p className={cn(dim, 'text-center')}>
								Your agent appears here once it connects.
							</p>
						)}
						{mode === 'chat' ? (
							<ChatPane frame={frame} reduced={reduced} />
						) : (
							<TerminalPane frame={frame} reduced={reduced} />
						)}
					</div>
				</div>
				<div
					inert
					aria-hidden="true"
					className="border-border bg-card flex min-h-0 flex-col overflow-hidden rounded-lg border"
				>
					<p className="border-border flex items-center gap-1.5 border-b px-2.5 py-1.5 text-[11px]">
						<b className="font-heading text-foreground">Activity</b>
						<span className="text-success">· live</span>
					</p>
					<ul className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-hidden p-2">
						{rows.length === 0 && (
							<li className={cn(dim, 'text-[11px]')}>Waiting for calls…</li>
						)}
						{rows.map((r) => (
							<li
								key={r.trace}
								className="border-border border-l-success bg-background animate-arrive rounded-md border border-l-[3px] px-2 py-1 font-mono text-[10px] leading-relaxed"
							>
								<div className="flex items-center gap-1.5">
									<StatusGlyph tone="ok" />
									{r.api !== 'jentic' && <VendorMark slug={r.api} size="xs" />}
									<span className="text-foreground min-w-0 flex-1 truncate">
										{r.api} · {r.op}
									</span>
									<span className="text-success">{r.status}</span>
								</div>
								<div className={cn(dim, 'flex justify-between pl-5')}>
									<span>{DEMO_AGENT.name} · allowed</span>
									<span>{r.trace}</span>
								</div>
							</li>
						))}
					</ul>
					<p
						className={cn(
							'text-success border-success/60 mx-2 mb-2 rounded-md border border-dashed px-2 py-1 text-center text-[10.5px] transition-opacity duration-500',
							frame >= SUMMARY_FRAME ? 'opacity-100' : 'opacity-0',
						)}
					>
						3 calls · all allowed · 0 keys exposed
					</p>
				</div>
			</div>
		</div>
	);
}
