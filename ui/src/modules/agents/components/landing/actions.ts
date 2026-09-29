/**
 * The real actions the tour's CTAs open: the Agents section's own sheets,
 * trays and routes, so the tour adds no URL vocabulary of its own. A `null`
 * action needs an agent that doesn't exist yet; its CTA says so.
 */
export type LandingSurface = 'permissions' | 'mcp';

export interface LandingActions {
	/** Opens the real `AgentCreateSheet`. */
	onCreateAgent: () => void;
	/** Opens the Add-APIs tray for the selected agent; `null` with no agent. */
	onAddApis: (() => void) | null;
	/** Opens one of the selected agent's dock sheets; `null` with no agent. */
	onOpenSurface: ((surface: LandingSurface) => void) | null;
	/**
	 * Opens the Notifications menu. Optional: without it the tour presses the
	 * top-bar bell itself, once the overlay has closed.
	 */
	onOpenNotifications?: () => void;
	/** The selected agent's name, for CTA labels; `null` with no agent. */
	agentName: string | null;
	/** The Monitor Activity log, filtered to the selected agent's calls when there is one. */
	activityHref: string;
}
