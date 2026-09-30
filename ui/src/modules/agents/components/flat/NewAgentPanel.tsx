/**
 * NewAgentPanel — the one "New agent" surface: a right-hand sheet with two
 * routes in, as tabs.
 *
 * - **Register from the CLI** (recommended): the same `RegisterFlow` the
 *   zero-agents landing's card renders. It listens for an agent that
 *   registers after the panel opened, then offers its approval, then its first
 *   API; each exit from there closes the panel onto the fleet with the agent
 *   selected (and the Add-APIs flow open when asked). A denied agent sends it
 *   back to listening, the typed name kept. Closed mid-flow, an arrival simply
 *   stays pending in the fleet.
 * - **Create here**: the manual create form, whose draft survives a dismissal.
 *
 * Each opening starts on the host's `initialTab` with a fresh register flow.
 */
import { useId, useRef, useState } from 'react';
import { useReducedMotionConfig, type Transition } from 'framer-motion';
import { X } from 'lucide-react';
import { Badge, Button, SheetPrimitive, TabNav, type TabNavOption } from '@/shared/ui';
import type { AgentEntity, useApproveAgent, useDenyAgent } from '@/modules/agents/api';
import {
	AgentCreateActions,
	AgentCreateFields,
	useAgentCreateForm,
} from '@/modules/agents/components/AgentCreateForm';
import { EASE_OUT_SOFT } from '@/modules/agents/components/flat/GhostFleet';
import { RegisterFlow } from '@/modules/agents/components/flat/RegisterFlow';
import type { FirstAgentExit } from '@/modules/agents/lib/firstRun';
import { useRegisterPanel } from '@/modules/agents/lib/useRegisterPanel';

export type NewAgentTab = 'register' | 'create';

interface NewAgentPanelProps {
	open: boolean;
	onClose: () => void;
	/** The tab each opening starts on. */
	initialTab: NewAgentTab;
	/** "Create here": the agent just created, and whether to carry on into Add APIs. */
	onCreated: (agent: AgentEntity, opts: { addApis: boolean }) => void;
	/** "Create here": a name to start the draft from. */
	initialName?: string;
	approve: ReturnType<typeof useApproveAgent>;
	deny: Pick<ReturnType<typeof useDenyAgent>, 'variables' | 'isSuccess'>;
	/** Opens the page's deny dialog for the arrival. */
	onDeny: (agent: AgentEntity) => void;
	/** The approved arrival's exit: the panel has closed; show the fleet. */
	onExit: (agent: AgentEntity, exit: FirstAgentExit) => void;
	/** "+N more waiting": the panel has closed; show the fleet's pending agents. */
	onShowFleet: (agent: AgentEntity) => void;
}

export function NewAgentPanel({
	open,
	onClose,
	initialTab,
	onCreated,
	initialName,
	approve,
	deny,
	onDeny,
	onExit,
	onShowFleet,
}: NewAgentPanelProps) {
	const [tab, setTab] = useState<NewAgentTab>(initialTab);
	const [wasOpen, setWasOpen] = useState(open);
	if (open !== wasOpen) {
		setWasOpen(open);
		if (open) setTab(initialTab);
	}

	const form = useAgentCreateForm({ open, onClose, onCreated, initialName });
	const register = useRegisterPanel({ open, active: tab === 'register', approve, deny });
	const registerNameRef = useRef<HTMLInputElement>(null);

	const reducedMotion = useReducedMotionConfig() ?? false;
	const fade: Transition = reducedMotion ? { duration: 0 } : { duration: 0.3, ease: 'easeOut' };
	const morph: Transition = reducedMotion
		? { duration: 0 }
		: { duration: 0.56, ease: EASE_OUT_SOFT };

	const baseId = useId();
	const headingId = `${baseId}-heading`;
	const tabId = (value: NewAgentTab) => `${baseId}-tab-${value}`;
	const panelId = (value: NewAgentTab) => `${baseId}-panel-${value}`;
	const options: TabNavOption<NewAgentTab>[] = [
		{
			value: 'register',
			label: 'Register from the CLI',
			badge: <Badge className="px-1.5 py-0 font-sans text-[10px]">Recommended</Badge>,
		},
		{ value: 'create', label: 'Create here' },
	];

	const agent = register.agent;
	/** Leave the panel first, so what the host opens next lands on a dismissed sheet. */
	const leave = (then: (a: AgentEntity) => void) => {
		if (!agent) return;
		onClose();
		then(agent);
	};

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			side="right"
			ariaLabelledBy={headingId}
			initialFocus={tab === 'create' ? form.nameRef : registerNameRef}
			className="flex flex-col"
		>
			<header className="border-border border-b px-5 pt-4">
				<div className="flex items-start justify-between gap-3">
					<h2 id={headingId} className="text-foreground text-lg font-semibold">
						New agent
					</h2>
					<Button
						variant="ghost"
						size="sm"
						aria-label="Close"
						onClick={onClose}
						className="text-muted-foreground hover:text-foreground -mr-2 shrink-0"
					>
						<X className="h-4 w-4" />
					</Button>
				</div>
				<TabNav
					options={options}
					value={tab}
					onChange={setTab}
					ariaLabel="How to add the agent"
					getTabId={tabId}
					getControls={panelId}
					className="-mx-2 mt-2 border-b-0"
				/>
			</header>

			<div
				role="tabpanel"
				id={panelId(tab)}
				aria-labelledby={tabId(tab)}
				data-testid={`new-agent-panel-${tab}`}
				data-phase={tab === 'register' ? register.phase : undefined}
				className="flex-1 overflow-y-auto p-5"
			>
				{tab === 'register' ? (
					<RegisterFlow
						baseId={baseId}
						agent={agent}
						onApprove={() => {
							if (agent) approve.mutate(agent.id);
						}}
						approvePending={register.approving}
						onDeny={() => {
							if (agent) onDeny(agent);
						}}
						onExit={(exit) => leave((a) => onExit(a, exit))}
						registerName={register.registerName}
						onRegisterNameChange={register.setRegisterName}
						expectedName={register.commandName}
						morePending={register.morePending}
						onShowFleet={() => leave(onShowFleet)}
						surface="panel"
						reducedMotion={reducedMotion}
						fade={fade}
						morph={morph}
						nameInputRef={registerNameRef}
					/>
				) : (
					<AgentCreateFields form={form} />
				)}
			</div>

			{tab === 'create' && (
				<footer className="border-border flex flex-wrap items-center justify-end gap-2 border-t p-5">
					<AgentCreateActions form={form} onCancel={onClose} />
				</footer>
			)}
		</SheetPrimitive>
	);
}
