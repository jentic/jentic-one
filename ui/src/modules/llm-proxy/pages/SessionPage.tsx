/**
 * SessionPage — Level 2 of the LLM Proxy surface: the trace-flow playground.
 *
 * Reads one SessionBundle by route id and lays out a summary strip + the
 * interactive TraceFlow, with two right-side deep-dive drawers (a tool call
 * and a chat turn). Handles loading / error / not-found gracefully.
 */
import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { ArrowLeft, Workflow } from 'lucide-react';
import {
	PageShell,
	PageHeader,
	PageHelp,
	AppLink,
	LoadingState,
	ErrorAlert,
	EmptyState,
	Badge,
} from '@/shared/ui';
import { ROUTES } from '@/shared/app/routes';
import { useSession } from '@/modules/llm-proxy/api';
import type { ChatTurn, ProxyAgent, ProxyCall } from '@/modules/llm-proxy/api';
import { formatTimestamp } from '@/modules/llm-proxy/lib/format';
import { SessionSummaryBar } from '@/modules/llm-proxy/components/SessionSummaryBar';
import { TraceFlow } from '@/modules/llm-proxy/components/TraceFlow';
import { CallDetailDrawer } from '@/modules/llm-proxy/components/CallDetailDrawer';
import { ChatTurnDrawer } from '@/modules/llm-proxy/components/ChatTurnDrawer';
import { AgentDetailDrawer } from '@/modules/llm-proxy/components/AgentDetailDrawer';
import { FinalResultDrawer } from '@/modules/llm-proxy/components/FinalResultDrawer';

function BackLink() {
	return (
		<AppLink
			href={ROUTES.llmProxy}
			className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1.5 text-sm font-medium"
		>
			<ArrowLeft className="h-4 w-4" />
			All sessions
		</AppLink>
	);
}

export default function SessionPage() {
	const { sessionId } = useParams();
	const { data, isLoading, error, refetch } = useSession(sessionId);

	const [activeCall, setActiveCall] = useState<ProxyCall | null>(null);
	const [callOpen, setCallOpen] = useState(false);
	const [activeTurn, setActiveTurn] = useState<ChatTurn | null>(null);
	const [chatOpen, setChatOpen] = useState(false);
	const [activeAgent, setActiveAgent] = useState<ProxyAgent | null>(null);
	const [agentOpen, setAgentOpen] = useState(false);
	const [resultOpen, setResultOpen] = useState(false);
	// The canvas element while it is in native fullscreen — drawers portal into
	// it then (so they overlay the fullscreen canvas in the browser's top
	// layer). `null` when not fullscreen → drawers portal to `document.body`.
	const [drawerContainer, setDrawerContainer] = useState<HTMLElement | null>(null);

	const chat = data?.chat ?? [];
	const agents = data?.agents ?? [];
	const allCalls = data?.calls ?? [];
	const hasChat = chat.length > 0;

	/** Resolve a call's chat turn: prefer its `turn_id`; else the same-agent's
	 *  first turn; else the session's first turn (graceful fallback). */
	const resolveTurnForCall = (call: ProxyCall): ChatTurn | null => {
		if (call.turn_id) {
			const exact = chat.find((t) => t.turn_id === call.turn_id);
			if (exact) return exact;
		}
		const sameAgent = chat.find((t) => t.agent_id === call.agent_id);
		return sameAgent ?? chat[0] ?? null;
	};

	/** The agent's representative turn: its OWN earliest turn (by ts), so the
	 *  Chat button opens the same agent's transcript slice that the Agent-detail
	 *  drawer lists (drawer filters chat by `agent_id === agent.id`). Falls back
	 *  to the session's first turn only when this agent genuinely owns none, so
	 *  the button and the drawer count never disagree. `ts` is epoch-seconds on
	 *  the real captured runs and an ISO string on the demo sessions — normalise
	 *  both to ms before comparing. */
	const tsToMs = (ts: ChatTurn['ts']): number => {
		if (ts == null) return NaN;
		if (typeof ts === 'number') return ts * 1000;
		return /^\d+(\.\d+)?$/.test(ts) ? Number(ts) * 1000 : new Date(ts).getTime();
	};
	const representativeTurnFor = (agent: ProxyAgent): ChatTurn | null => {
		const own = chat
			.filter((t) => t.agent_id === agent.id)
			.sort((a, b) => tsToMs(a.ts) - tsToMs(b.ts));
		return own[0] ?? chat[0] ?? null;
	};

	const openCall = (call: ProxyCall) => {
		setActiveCall(call);
		setCallOpen(true);
	};
	const openTurn = (turn: ChatTurn) => {
		setActiveTurn(turn);
		setChatOpen(true);
	};
	const openChatForAgent = (agent: ProxyAgent) => {
		const turn = representativeTurnFor(agent);
		if (turn) openTurn(turn);
	};
	const openChatFromCall = (call: ProxyCall) => {
		const turn = resolveTurnForCall(call);
		if (turn) {
			setCallOpen(false);
			openTurn(turn);
		}
	};
	const selectAgent = (agent: ProxyAgent) => {
		setActiveAgent(agent);
		setAgentOpen(true);
	};

	const help = (
		<PageHelp
			title="Session trace-flow"
			intro="A visual replay of one agent run: what your agents did, and whether governance behaved."
			sections={[
				{
					heading: 'Reading the flow',
					body: 'The root agent sits on the left, centered against its subtree; its subagents branch out to the right. Each agent\u2019s tool calls flow left\u2192right as coloured blocks that merge into that agent\u2019s output. The flow then CONVERGES: every leaf agent feeds a connector into a single \u201cResult\u201d node at the far right \u2014 the run\u2019s final deliverable. A persistent \u201cOutcome\u201d strip pinned to the top of the canvas previews it too; click either to read the full closing synthesis. Use \u201cWorkers\u201d to reveal an agent\u2019s subagents, and each node\u2019s calls toggle to hide/show its own tool-call chain. A single-agent session shows that one agent on its own, still feeding the Result node.',
				},
				{
					heading: 'Colours',
					body: 'Green blocks were allowed, red were denied by governance, amber errored (e.g. an upstream 429). A small warning glyph marks destructive calls.',
				},
				{
					heading: 'Navigating',
					body: 'Hover an agent for its rollup stats; click an agent to open its full detail (own vs. rollup stats, its tool calls and chat turns). Click any call block for the full deep-dive (request/response, timeline, credential, verdict reason, tokens, cost). Drag any agent to reposition it; its connectors follow. Zoom with the buttons, Ctrl/\u2318 + scroll, or +/-/0 when focused; hold space (or middle-drag) to pan; and use the fullscreen button (Esc exits) for an immersive view. Reset (the % button) restores the clean layout.',
				},
			]}
		/>
	);

	if (isLoading) {
		return (
			<PageShell>
				<PageHeader title="Session" subtitle="Loading trace-flow…" actions={help} />
				<BackLink />
				<LoadingState message="Loading session…" />
			</PageShell>
		);
	}

	if (error) {
		return (
			<PageShell>
				<PageHeader title="Session" subtitle="Trace-flow playground." actions={help} />
				<BackLink />
				<ErrorAlert
					message={error instanceof Error ? error : 'Failed to load session'}
					onRetry={() => void refetch()}
				/>
			</PageShell>
		);
	}

	if (!data) {
		return (
			<PageShell>
				<PageHeader title="Session not found" actions={help} />
				<BackLink />
				<EmptyState
					icon={<Workflow className="h-6 w-6" />}
					title="No such session"
					description="This session id doesn’t match any run. It may have been pruned or the link is stale."
					action={<BackLink />}
				/>
			</PageShell>
		);
	}

	const { session } = data;
	const subtitle = `Started ${formatTimestamp(session.started_at)}`;

	return (
		<PageShell className="flex h-[calc(100dvh-6rem)] flex-col pb-2" spacing="">
			<div className="space-y-6">
				<PageHeader
					title={session.title}
					subtitle={subtitle}
					actions={
						<div className="flex items-center gap-2">
							<Badge
								variant={session.status === 'completed' ? 'success' : 'pending'}
								dot
							>
								{session.status}
							</Badge>
							{help}
						</div>
					}
				/>

				<BackLink />

				<SessionSummaryBar agents={agents} calls={allCalls} />
			</div>

			{agents.length === 0 ? (
				<EmptyState
					icon={<Workflow className="h-6 w-6" />}
					title="No agents in this session"
					description="This run recorded no agent activity."
				/>
			) : (
				<div className="mt-4 flex min-h-0 flex-1 flex-col">
					<TraceFlow
						agents={agents}
						calls={allCalls}
						onOpenCall={openCall}
						onOpenChat={openChatForAgent}
						onSelectAgent={selectAgent}
						finalOutput={data.final_output}
						onOpenResult={() => setResultOpen(true)}
						onFullscreenChange={setDrawerContainer}
					/>
				</div>
			)}

			<AgentDetailDrawer
				agent={activeAgent}
				calls={activeAgent ? allCalls.filter((c) => c.agent_id === activeAgent.id) : []}
				turns={activeAgent ? chat.filter((t) => t.agent_id === activeAgent.id) : []}
				parent={
					activeAgent?.parent_id
						? (agents.find((a) => a.id === activeAgent.parent_id) ?? null)
						: null
				}
				open={agentOpen}
				onClose={() => setAgentOpen(false)}
				onOpenCall={openCall}
				onOpenTurn={openTurn}
				container={drawerContainer}
			/>

			<CallDetailDrawer
				call={activeCall}
				open={callOpen}
				onClose={() => setCallOpen(false)}
				onViewChat={hasChat ? openChatFromCall : undefined}
				container={drawerContainer}
			/>
			<ChatTurnDrawer
				turn={activeTurn}
				open={chatOpen}
				onClose={() => setChatOpen(false)}
				container={drawerContainer}
			/>

			<FinalResultDrawer
				finalOutput={data.final_output}
				open={resultOpen}
				onClose={() => setResultOpen(false)}
				container={drawerContainer}
			/>
		</PageShell>
	);
}
