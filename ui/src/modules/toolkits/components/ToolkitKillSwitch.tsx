import { KillSwitch } from '@/shared/ui';
import { useSetToolkitActive } from '@/modules/toolkits/api';

/**
 * Toolkit-level kill switch — suspends or restores ALL access for a toolkit
 * by flipping its `active` flag, which the broker enforces for both toolkit
 * API keys and agent-identity callers. A thin wrapper binding the shared
 * `KillSwitch` control to the toolkit mutation and copy.
 */
export interface ToolkitKillSwitchProps {
	toolkitId: string;
	active: boolean;
	className?: string;
}

export function ToolkitKillSwitch({ toolkitId, active, className }: ToolkitKillSwitchProps) {
	const setActive = useSetToolkitActive(toolkitId);
	return (
		<KillSwitch
			active={active}
			pending={setActive.isPending}
			onToggle={(next) => setActive.mutate(next)}
			suspendAriaLabel="Suspend toolkit (kill switch)"
			restoreAriaLabel="Restore toolkit access"
			suspendPrompt="Block keys + agents?"
			restorePrompt="Restore access?"
			className={className}
		/>
	);
}
