/**
 * AgentsLanding — the product tour's body: two acts that say why a gateway
 * and how to set it up.
 *
 *   1 Why Jentic One — one job, without vs with the gateway (plays by itself)
 *   2 Set it up      — a tour of the six real steps, each CTA opens the real thing
 *
 * It lives in the `AgentsTour` overlay, which renders the act tabs (from
 * `LandingTabs`) in its header row and mounts this only while open, so each
 * opening starts from Act 1's typing intro and every clock stops on close.
 */
import { AnimatePresence, motion } from 'framer-motion';
import { TabNav, type TabNavOption } from '@/shared/ui';
import type { LandingActions } from '@/modules/agents/components/landing/actions';
import { WhyAct } from '@/modules/agents/components/landing/act1/WhyAct';
import { SetupAct } from '@/modules/agents/components/landing/act2/SetupAct';
import { EASE_OUT_SOFT } from '@/modules/agents/components/landing/motion';

export type LandingAct = 'why' | 'setup';

const ACTS: TabNavOption<LandingAct>[] = [
	{ value: 'why', label: 'Why Jentic One' },
	{ value: 'setup', label: 'Set it up' },
];

const tabId = (baseId: string, v: LandingAct) => `${baseId}-tab-${v}`;
const panelId = (baseId: string, v: LandingAct) => `${baseId}-panel-${v}`;

/** The act tabs; `baseId` pairs them with `AgentsLanding`'s panel. */
export function LandingTabs({
	baseId,
	act,
	onChange,
}: {
	baseId: string;
	act: LandingAct;
	onChange: (act: LandingAct) => void;
}) {
	return (
		<TabNav
			options={ACTS}
			value={act}
			onChange={onChange}
			ariaLabel="Tour sections"
			getTabId={(v) => tabId(baseId, v)}
			getControls={(v) => panelId(baseId, v)}
			className="border-b-0"
		/>
	);
}

export interface AgentsLandingProps {
	baseId: string;
	act: LandingAct;
	onActChange: (act: LandingAct) => void;
	actions: LandingActions;
	reducedMotion: boolean;
}

export function AgentsLanding({
	baseId,
	act,
	onActChange,
	actions,
	reducedMotion: reduced,
}: AgentsLandingProps) {
	return (
		<AnimatePresence mode="wait" initial={false}>
			<motion.div
				key={act}
				role="tabpanel"
				id={panelId(baseId, act)}
				aria-labelledby={tabId(baseId, act)}
				data-testid="agents-landing"
				initial={reduced ? false : { opacity: 0, y: 6 }}
				animate={{ opacity: 1, y: 0 }}
				exit={reduced ? undefined : { opacity: 0, y: 6 }}
				transition={{ duration: reduced ? 0 : 0.22, ease: EASE_OUT_SOFT }}
			>
				{act === 'why' && (
					<WhyAct reducedMotion={reduced} onNext={() => onActChange('setup')} />
				)}
				{act === 'setup' && <SetupAct actions={actions} reducedMotion={reduced} />}
			</motion.div>
		</AnimatePresence>
	);
}
