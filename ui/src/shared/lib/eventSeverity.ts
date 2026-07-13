import { Info, AlertTriangle, OctagonAlert, Flame, type LucideIcon } from 'lucide-react';
import { EventSeverity } from '@/shared/api';

/**
 * The canonical icon for each platform-event severity — the SINGLE source of
 * truth shared by every surface that renders an event (Monitor's Events tab and
 * the Dashboard's "Needs attention" card). Keeping the icon here is what lets
 * those surfaces drop the redundant severity *word* and rely on the glyph
 * alone: the same event reads identically wherever it appears.
 *
 * Only the ICON is shared. The colour/tint treatment is intentionally left to
 * each surface (Monitor maps severity to its own `MonitorAccent`; the Dashboard
 * uses medallion ring classes) because those are surface-specific design
 * decisions, not part of the event's identity.
 */
const SEVERITY_ICON: Record<EventSeverity, LucideIcon> = {
	[EventSeverity.INFO]: Info,
	[EventSeverity.WARNING]: AlertTriangle,
	[EventSeverity.ERROR]: OctagonAlert,
	[EventSeverity.CRITICAL]: Flame,
};

/** The icon for a severity, defaulting to the INFO glyph for unknown values. */
export function eventSeverityIcon(severity: EventSeverity | string): LucideIcon {
	return SEVERITY_ICON[severity as EventSeverity] ?? SEVERITY_ICON[EventSeverity.INFO];
}
