import { describe, expect, it } from 'vitest';
import { Info, AlertTriangle, OctagonAlert, Flame } from 'lucide-react';
import { EventSeverity } from '@/shared/api';
import { eventSeverityIcon } from '../eventSeverity';

/**
 * Pins the canonical per-severity glyph. This map is the single source of truth
 * shared by Monitor's Events tab and the Dashboard's "Needs attention" card, so
 * if a glyph is ever changed here a reviewer sees the same change land in both
 * surfaces at once — that's the whole point of the extraction.
 */
describe('eventSeverityIcon', () => {
	it('maps each known severity to its distinct glyph', () => {
		expect(eventSeverityIcon(EventSeverity.INFO)).toBe(Info);
		expect(eventSeverityIcon(EventSeverity.WARNING)).toBe(AlertTriangle);
		expect(eventSeverityIcon(EventSeverity.ERROR)).toBe(OctagonAlert);
		expect(eventSeverityIcon(EventSeverity.CRITICAL)).toBe(Flame);
	});

	it('falls back to the INFO glyph for unknown / unexpected severities', () => {
		// The backend could add a severity the UI does not yet know about, or a
		// raw string could slip through — never crash, default to Info.
		expect(eventSeverityIcon('totally-new-severity')).toBe(Info);
		expect(eventSeverityIcon('' as EventSeverity)).toBe(Info);
	});
});
