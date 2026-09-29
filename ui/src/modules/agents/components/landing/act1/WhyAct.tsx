/**
 * Act 1 — Why Jentic One. You give research-bot a task (typed into a composer
 * that settles into the task bar), then one event timeline drives both the
 * lanes and the Monitor beside them: every agent-side event and its Monitor
 * effect land in the same beat, the matching call carries the same tag in both
 * places, and a callout points at the Monitor as each event lands. Jentic One
 * is the primary lane; the WITHOUT lane under it is the quieter comparison.
 *
 * The story loops without stopping (past the typed intro). Only the viewer's
 * own Pause, the tab being hidden or the stage scrolled away hold its clock;
 * a Monitor hand-off or the composer's send already under way finishes on its
 * own short timer. Under reduced motion nothing plays: there is no typing
 * intro, and the beats are annotated stills stepped with Previous / Next.
 */
import { useRef, useState } from 'react';
import { AnimatePresence, LayoutGroup, motion } from 'framer-motion';
import { ArrowRight } from 'lucide-react';
import { Button } from '@/shared/ui';
import { useLaneGeometry } from '@/modules/agents/components/landing/act1/laneGeometry';
import { WithLane } from '@/modules/agents/components/landing/act1/WithLane';
import { WithoutLane } from '@/modules/agents/components/landing/act1/WithoutLane';
import {
	MonitorFlight,
	type MonitorLanding,
} from '@/modules/agents/components/landing/act1/MonitorFlight';
import {
	ComparisonStrip,
	TaskBar,
	TaskComposer,
} from '@/modules/agents/components/landing/act1/StoryParts';
import { WHY_LOOP_FROM, WHY_SCRIPT } from '@/modules/agents/components/landing/act1/script';
import { DEMO_AGENT, type DemoApiId } from '@/modules/agents/components/landing/data/demoFixtures';
import { LandingCaption, spaceToggles } from '@/modules/agents/components/landing/LandingControls';
import { EASE_OUT_SOFT } from '@/modules/agents/components/landing/motion';
import { OperatorPanel } from '@/modules/agents/components/landing/OperatorPanel';
import { useLandingTimeline } from '@/modules/agents/components/landing/useLandingTimeline';

export interface WhyActProps {
	reducedMotion?: boolean;
	onNext: () => void;
}

export function WhyAct({ reducedMotion, onNext }: WhyActProps) {
	const timeline = useLandingTimeline(WHY_SCRIPT, {
		reducedMotion,
		loop: true,
		loopFrom: WHY_LOOP_FROM,
		pauseOnFocus: false,
	});
	const { state, reduced } = timeline;
	const sceneRef = useRef<HTMLDivElement | null>(null);
	const [hoverApi, setHoverApi] = useState<DemoApiId | null>(null);
	const [landing, setLanding] = useState<MonitorLanding | null>(null);
	const [lanes, measureLanes] = useLaneGeometry();
	// Reduced motion skips the typing intro: the task bar is there from the start.
	const intro = state.intro && !reduced;
	return (
		<div className="space-y-3" onKeyDown={spaceToggles(timeline.toggle)}>
			<LayoutGroup id="agents-landing-why">
				{intro ? (
					<div className="py-6">
						<TaskComposer
							reduced={reduced}
							onSend={() => timeline.seek(WHY_LOOP_FROM)}
						/>
					</div>
				) : (
					<TaskBar timeline={timeline} hoverApi={hoverApi} />
				)}
			</LayoutGroup>

			{/* The stage (not the task bar): scrolling it away holds the clock. */}
			<div ref={timeline.stageRef} data-testid="why-stage" data-beat={timeline.beat?.id}>
				<AnimatePresence initial={false}>
					{!intro && (
						<motion.div
							key="scene"
							initial={reduced ? false : { opacity: 0, y: 12 }}
							animate={{ opacity: 1, y: 0 }}
							exit={reduced ? undefined : { opacity: 0, y: 8 }}
							transition={{
								duration: reduced ? 0 : 0.4,
								ease: EASE_OUT_SOFT,
								delay: reduced ? 0 : 0.15,
							}}
							ref={sceneRef}
							className="relative grid items-stretch gap-3 xl:grid-cols-[minmax(0,1fr)_18rem]"
						>
							<div className="min-w-0 space-y-3">
								<WithLane
									state={state}
									reduced={reduced}
									g={lanes}
									laneRef={measureLanes}
									hoverApi={hoverApi}
								/>
								<WithoutLane state={state} reduced={reduced} g={lanes} />
								<ComparisonStrip state={state} reduced={reduced} />
								<div className="flex flex-wrap items-center gap-3">
									<Button size="sm" onClick={onNext}>
										How do I set this up?
										<ArrowRight className="h-4 w-4" aria-hidden="true" />
									</Button>
									<span className="text-muted-foreground text-xs">
										Six steps, a few minutes, each one opens the real screen.
									</span>
								</div>
							</div>
							<OperatorPanel
								className="xl:h-full"
								agent={{ name: DEMO_AGENT.name, status: state.agentStatus }}
								pointer={state.pointer}
								reduced={reduced}
								bell={state.bell}
								log={state.log}
								pulse={state.pulse}
								onHoverRow={(api) => setHoverApi(api ?? null)}
								landing={landing}
							/>
							<MonitorFlight
								callout={state.callout}
								beatKey={timeline.beat?.id}
								containerRef={sceneRef}
								reduced={reduced}
								onLanding={setLanding}
							/>
						</motion.div>
					)}
				</AnimatePresence>
			</div>
			{intro && <LandingCaption caption={timeline.beat?.caption} className="sr-only" />}
		</div>
	);
}
