/**
 * Act 2 — Set it up. A self-playing, looping tour of the six real steps: a
 * numbered step list (click to jump), a browser-style frame holding a
 * miniature of the real screen with a scripted cursor, what you do and what
 * Jentic does, and a CTA that opens the real thing.
 */
import { useCallback, useRef, useState, type ReactNode } from 'react';
import { ArrowRight, Bell, Check, MessageSquare, SquareTerminal } from 'lucide-react';
import { AppLink, Button, SegmentedToggle, Tooltip } from '@/shared/ui';
import { ROUTES, ROUTE_PATHS } from '@/shared/app';
import { cn } from '@/shared/lib/utils';
import { DcrQuickstart } from '@/modules/agents/components/DcrQuickstart';
import type { LandingActions } from '@/modules/agents/components/landing/actions';
import {
	CONNECT_SPLIT_FRAME,
	ConnectStage,
	type ConnectMode,
} from '@/modules/agents/components/landing/act2/ConnectStage';
import {
	ApisStage,
	CreateStage,
	GovernStage,
	KeysStage,
	RulesStage,
	type MiniProps,
} from '@/modules/agents/components/landing/act2/SetupStage';
import { BrowserFrame, StageCursor } from '@/modules/agents/components/landing/act2/StageKit';
import {
	SETUP_SCRIPT,
	SETUP_STEPS,
	setupFrame,
	stepStartBeat,
	stepStartIndex,
} from '@/modules/agents/components/landing/act2/script';
import {
	BeatSpanProgress,
	LandingControls,
	spaceToggles,
} from '@/modules/agents/components/landing/LandingControls';
import { useLandingTimeline } from '@/modules/agents/components/landing/useLandingTimeline';

const eyebrow = 'text-muted-foreground text-xs tracking-wider uppercase';

/** A CTA that needs an agent first: disabled, with the reason on hover/focus. */
function NeedsAgent({ label }: { label: string }) {
	return (
		<Tooltip content="Create an agent first" interactiveChild>
			<Button size="sm" disabled>
				{label}
			</Button>
		</Tooltip>
	);
}

function StepCta({ step, actions }: { step: number; actions: LandingActions }) {
	const {
		onCreateAgent,
		onAddApis,
		onOpenSurface,
		onOpenNotifications,
		agentName,
		activityHref,
	} = actions;
	switch (SETUP_STEPS[step].id) {
		case 'create':
			return (
				<Button size="sm" onClick={onCreateAgent}>
					{agentName ? 'Create another agent' : 'Create your first agent'}
					<ArrowRight className="h-4 w-4" aria-hidden="true" />
				</Button>
			);
		case 'apis':
			return (
				<span className="flex flex-wrap items-center gap-2">
					{onAddApis ? (
						<Button size="sm" onClick={onAddApis}>
							Add APIs to {agentName}
						</Button>
					) : (
						<Button size="sm" onClick={onCreateAgent}>
							Create an agent first
						</Button>
					)}
					<AppLink href={ROUTES.discover} variant="ghost" size="sm">
						Browse the catalog
					</AppLink>
				</span>
			);
		case 'keys':
			return (
				<AppLink
					href={ROUTE_PATHS.credentialInventory({ create: true })}
					variant="primary"
					size="sm"
				>
					Add a credential
				</AppLink>
			);
		case 'rules':
			return onOpenSurface ? (
				<Button size="sm" onClick={() => onOpenSurface('permissions')}>
					Open permissions for {agentName}
				</Button>
			) : (
				<NeedsAgent label="Open permissions" />
			);
		case 'connect':
			return onOpenSurface ? (
				<Button size="sm" onClick={() => onOpenSurface('mcp')}>
					Connect {agentName} over MCP
				</Button>
			) : (
				<NeedsAgent label="Connect over MCP" />
			);
		case 'govern':
			return (
				<span className="flex flex-wrap items-center gap-2">
					<AppLink href={activityHref} variant="primary" size="sm">
						Open the Activity log
					</AppLink>
					<Button size="sm" variant="ghost" onClick={onOpenNotifications}>
						<Bell className="h-4 w-4" aria-hidden="true" />
						Open notifications
					</Button>
				</span>
			);
	}
}

function Stage({
	step,
	frame,
	mode,
	reduced,
	pressing,
	toggle,
}: MiniProps & { step: number; mode: ConnectMode; toggle: ReactNode }) {
	const p = { frame, reduced, pressing };
	switch (SETUP_STEPS[step].id) {
		case 'create':
			return <CreateStage {...p} />;
		case 'apis':
			return <ApisStage {...p} />;
		case 'keys':
			return <KeysStage {...p} />;
		case 'rules':
			return <RulesStage {...p} />;
		case 'connect':
			return <ConnectStage {...p} mode={mode} toggle={toggle} />;
		case 'govern':
			return <GovernStage {...p} />;
	}
}

export interface SetupActProps {
	actions: LandingActions;
	reducedMotion?: boolean;
}

export function SetupAct({ actions, reducedMotion }: SetupActProps) {
	const timeline = useLandingTimeline(SETUP_SCRIPT, { reducedMotion, loop: true });
	const { step, frame } = timeline.state;
	const [mode, setMode] = useState<ConnectMode>('chat');
	const screenRef = useRef<HTMLDivElement | null>(null);
	const { stageRef } = timeline;
	// One stable ref for both the cursor and the clock's visibility/focus checks.
	const setScreen = useCallback(
		(el: HTMLDivElement | null) => {
			screenRef.current = el;
			stageRef.current = el;
		},
		[stageRef],
	);
	const current = SETUP_STEPS[step];
	const meta = setupFrame(step, frame);
	const pressing = meta.press ? meta.cursor : undefined;

	function switchMode(next: ConnectMode) {
		setMode(next);
		// Replay the split view in the chosen client.
		timeline.seek(`connect-${CONNECT_SPLIT_FRAME}`);
		timeline.play();
	}

	// A stage control: using it is watching the tour, so its focus doesn't hold the clock.
	const toggle = (
		<div data-stage-control="">
			<SegmentedToggle
				options={[
					{
						value: 'chat',
						label: 'Chat',
						icon: <MessageSquare className="h-3.5 w-3.5" />,
					},
					{
						value: 'terminal',
						label: 'Terminal',
						icon: <SquareTerminal className="h-3.5 w-3.5" />,
					},
				]}
				value={mode}
				onChange={switchMode}
				ariaLabel="Client"
			/>
		</div>
	);

	return (
		<div className="space-y-4" onKeyDown={spaceToggles(timeline.toggle)}>
			<div className="grid items-start gap-4 lg:grid-cols-[17rem_minmax(0,1fr)]">
				<div className="space-y-3">
					<div>
						<h3 className="font-heading text-foreground text-base font-semibold">
							Here’s how you set it up.
						</h3>
						<p className="text-warning text-sm">Six steps, about five minutes.</p>
					</div>
					<ol className="space-y-1.5" aria-label="Setup steps">
						{SETUP_STEPS.map((s, i) => {
							const active = i === step;
							const done = i < step;
							return (
								<li key={s.id}>
									<Button
										variant="ghost"
										fullWidth
										onClick={() => timeline.seek(stepStartBeat(i))}
										aria-current={active ? 'step' : undefined}
										className={cn(
											'relative h-auto justify-start gap-2.5 overflow-hidden rounded-xl border px-3 py-2.5 text-left text-sm transition-colors duration-200',
											active
												? 'border-primary/60 bg-primary/[0.06] text-foreground hover:bg-primary/[0.08]'
												: 'border-border bg-card text-muted-foreground hover:text-foreground',
										)}
									>
										<span
											className={cn(
												'flex h-6 w-6 shrink-0 items-center justify-center rounded-full font-mono text-[11px] font-bold transition-colors duration-200',
												done
													? 'bg-success text-background'
													: active
														? 'bg-primary text-primary-foreground'
														: 'bg-muted text-muted-foreground',
											)}
										>
											{done ? (
												<Check className="h-3.5 w-3.5" aria-hidden="true" />
											) : (
												i + 1
											)}
											{done && <span className="sr-only">done: </span>}
										</span>
										{s.title}
										{active && (
											// The thin bar along the active step's bottom edge.
											<BeatSpanProgress
												timeline={timeline}
												first={stepStartIndex(i)}
												length={s.frames.length}
												className="bg-primary inset-x-0 bottom-0 h-0.5"
											/>
										)}
									</Button>
								</li>
							);
						})}
					</ol>
					<LandingControls timeline={timeline} noun="tour" />
				</div>

				<BrowserFrame
					url={current.url}
					footer={
						<div className="border-border bg-card grid gap-3 border-t px-4 py-3 text-sm sm:grid-cols-[1fr_1fr_auto] sm:items-center">
							<div>
								<p className={cn(eyebrow, 'text-warning')}>You do</p>
								<p className="text-foreground mt-0.5 text-xs leading-relaxed">
									{current.you}
								</p>
							</div>
							<div>
								<p className={cn(eyebrow, 'text-success')}>Jentic</p>
								<p className="text-foreground mt-0.5 text-xs leading-relaxed">
									{current.jentic}
								</p>
							</div>
							<div data-testid="setup-cta">
								<StepCta step={step} actions={actions} />
							</div>
						</div>
					}
				>
					<div
						ref={setScreen}
						className="relative h-80 overflow-hidden p-4"
						data-testid="setup-stage"
						data-step={current.id}
						data-frame={frame}
					>
						<Stage
							step={step}
							frame={frame}
							mode={mode}
							reduced={timeline.reduced}
							pressing={pressing}
							toggle={toggle}
						/>
						{current.id !== 'connect' || frame < CONNECT_SPLIT_FRAME ? (
							<StageCursor
								stage={screenRef}
								target={meta.cursor}
								press={meta.press}
								pressKey={timeline.beat?.id ?? ''}
								reduced={timeline.reduced}
							/>
						) : null}
					</div>
				</BrowserFrame>
			</div>

			<DcrQuickstart />
		</div>
	);
}
