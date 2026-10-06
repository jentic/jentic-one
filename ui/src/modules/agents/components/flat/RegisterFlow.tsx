/**
 * RegisterFlow — the self-registration flow both the zero-agents landing's
 * primary card and the New agent panel's "Register from the CLI" tab render:
 * the register command (listening), then the arrived agent's details with
 * Approve / Deny (arrived), then its first API (approved). While listening,
 * the four-step stepper, the live status line and the collapsed CLI install
 * hint sit under the command; once an agent is on the card, it shows its own
 * one-line progress and the status line is only announced.
 *
 * The host decides which agent is `agent` and what its exits do. An in-session
 * view change — a new phase, or the next pending agent replacing a denied one —
 * swaps content out from under the keyboard (the Approve or Deny button focused
 * a moment ago is gone), so focus moves to the new view's heading; the view the
 * flow mounted in keeps focus where it is. A change made from a modal
 * (the deny dialog) lands once it closes — its own focus return targets a
 * trigger that is on its way out.
 */
import { useEffect, useRef, type RefObject } from 'react';
import { AnimatePresence, motion, type Transition } from 'framer-motion';
import type { AgentEntity } from '@/modules/agents/api';
import {
	AgentDetails,
	CliInstallHint,
	RegisterCommand,
	StatusLine,
	Stepper,
	type RegisterSurface,
} from '@/modules/agents/components/flat/firstAgentParts';
import type { FirstAgentExit, FirstAgentPhase } from '@/modules/agents/lib/firstRun';

/**
 * The id of the flow's current heading, so the host's region can be labelled
 * by it. One id per view (keyed like the view): during the crossfade both views
 * are mounted, so a fixed id would be duplicated.
 */
export function registerFlowTitleId(baseId: string, agent: AgentEntity | null): string {
	return `${baseId}-${agent ? agent.id : 'command'}`;
}

export interface RegisterFlowProps {
	/** A `useId()` of the host's; see {@link registerFlowTitleId}. */
	baseId: string;
	/** The agent being finished: pending or active, or null while listening. */
	agent: AgentEntity | null;
	onApprove: () => void;
	approvePending: boolean;
	/** Opens the page's deny dialog for `agent`. */
	onDeny: () => void;
	/** The operator's choice once the agent is approved. */
	onExit: (exit: FirstAgentExit) => void;
	/** The name typed for the command. */
	registerName: string;
	onRegisterNameChange: (name: string) => void;
	/** The name the displayed command registers with. */
	commandName: string;
	/** The existing agent name the typed one duplicates, or `null`. */
	registerNameDuplicateOf: string | null;
	/** The name an arrival should carry (the command's), or null when this
	 * session never showed the command. */
	expectedName: string | null;
	/** Other agents waiting for approval besides `agent`. */
	morePending: number;
	/** Leave for the fleet view, where every pending agent is listed. */
	onShowFleet: () => void;
	/** Where the flow is shown — it words the stepper and header for it. */
	surface: RegisterSurface;
	reducedMotion: boolean;
	/** The crossfade between views. */
	fade: Transition;
	/** The layout morph the stepper block rides while the card resizes. */
	morph: Transition;
	/** The command's name input, for a host that focuses it on open. */
	nameInputRef?: RefObject<HTMLInputElement | null>;
}

export function RegisterFlow({
	baseId,
	agent,
	onApprove,
	approvePending,
	onDeny,
	onExit,
	registerName,
	onRegisterNameChange,
	commandName,
	registerNameDuplicateOf,
	expectedName,
	morePending,
	onShowFleet,
	surface,
	reducedMotion,
	fade,
	morph,
	nameInputRef,
}: RegisterFlowProps) {
	const agentPhase = agent?.status === 'pending' ? 'arrived' : 'approved';
	const phase: FirstAgentPhase = agent == null ? 'listening' : agentPhase;
	const titleId = registerFlowTitleId(baseId, agent);

	// The view is the phase *and* the agent: denying one pending agent while
	// another waits keeps the phase at 'arrived' but replaces the card.
	const viewKey = `${phase}:${agent?.id ?? ''}`;
	const lastViewRef = useRef(viewKey);
	useEffect(() => {
		if (lastViewRef.current === viewKey) return;
		lastViewRef.current = viewKey;
		const focusTitle = () => document.getElementById(titleId)?.focus({ preventScroll: true });
		const modal = document.querySelector('dialog[open]');
		if (!modal) {
			focusTitle();
			return;
		}
		const onModalClose = () => requestAnimationFrame(focusTitle);
		modal.addEventListener('close', onModalClose, { once: true });
		return () => modal.removeEventListener('close', onModalClose);
	}, [viewKey, titleId]);

	return (
		<>
			<AnimatePresence mode="popLayout" initial={false}>
				<motion.div
					key={agent ? `agent-${agent.id}` : 'command'}
					layout="position"
					initial={{ opacity: 0 }}
					animate={{ opacity: 1 }}
					exit={{ opacity: 0 }}
					transition={fade}
				>
					{agent ? (
						<AgentDetails
							titleId={titleId}
							agent={agent}
							phase={agentPhase}
							onApprove={onApprove}
							approvePending={approvePending}
							onDeny={onDeny}
							onExit={onExit}
							fade={fade}
							expectedName={expectedName}
							morePending={morePending}
							onShowFleet={onShowFleet}
						/>
					) : (
						<RegisterCommand
							titleId={titleId}
							name={registerName}
							onNameChange={onRegisterNameChange}
							commandName={commandName}
							duplicateOf={registerNameDuplicateOf}
							surface={surface}
							inputRef={nameInputRef}
						/>
					)}
				</motion.div>
			</AnimatePresence>
			<motion.div layout="position" transition={{ layout: morph }}>
				{/* The full stepper only before an agent arrives; the agent card
				    carries its own one-line progress. */}
				{phase === 'listening' && (
					<Stepper phase={phase} reducedMotion={reducedMotion} surface={surface} />
				)}
				<StatusLine phase={phase} name={agent?.name ?? null} />
				<AnimatePresence initial={false}>
					{phase === 'listening' && (
						<motion.div
							key="cli-install"
							initial={{ opacity: 0 }}
							animate={{ opacity: 1 }}
							exit={{ opacity: 0 }}
							transition={fade}
						>
							<CliInstallHint reducedMotion={reducedMotion} />
						</motion.div>
					)}
				</AnimatePresence>
			</motion.div>
		</>
	);
}
