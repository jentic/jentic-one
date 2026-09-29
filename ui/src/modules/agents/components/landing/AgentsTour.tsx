/**
 * AgentsTour — the product tour ("Why Jentic One" + "Set it up") in a
 * near-full-screen overlay, opened from the Agents page header.
 *
 * Built on the app's `Dialog` (native modal `<dialog>`): focus is trapped,
 * Esc and the X close it, and focus returns to the opener. It covers the
 * shell (the Activity rail included), so no page chrome needs holding, and
 * body scroll is locked while it is up. The tour mounts only while open, so
 * every opening starts from Act 1's typing intro and every clock stops on
 * close. Stateless (no draft), so this conditional mount is the intended
 * lifecycle; closing also puts the act tabs back on "Why Jentic One".
 *
 * Its CTAs close the overlay first and then open the real sheet, drawer or
 * route on the next frame — after the dialog has handed focus back. Links in
 * the tour close it as they navigate.
 */
import { useEffect, useId, useState } from 'react';
import { useReducedMotionConfig } from 'framer-motion';
import { Dialog } from '@/shared/ui';
import type { LandingActions } from '@/modules/agents/components/landing/actions';
import {
	AgentsLanding,
	LandingTabs,
	type LandingAct,
} from '@/modules/agents/components/landing/AgentsLanding';

export interface AgentsTourProps {
	open: boolean;
	onClose: () => void;
	actions: LandingActions;
	/** Override the reduced-motion preference (tests). */
	reducedMotion?: boolean;
}

/** Wrap a CTA: close the overlay, then run it once the dialog has let go. */
function afterClose<A extends unknown[]>(
	onClose: () => void,
	run: (...args: A) => void,
): (...args: A) => void {
	return (...args) => {
		onClose();
		requestAnimationFrame(() => run(...args));
	};
}

/**
 * The top-bar bell owns its menu's open state; with no `onOpenNotifications`
 * the tour presses it. Only once the overlay is gone: while the modal is up,
 * the bell sits inert behind it.
 */
function pressTopBarBell(): void {
	document
		.querySelector<HTMLButtonElement>(
			'button[aria-haspopup="dialog"][aria-label^="Notifications"]',
		)
		?.click();
}

export function AgentsTour({ open, onClose, actions, reducedMotion }: AgentsTourProps) {
	const configReduced = useReducedMotionConfig();
	const reduced = reducedMotion ?? configReduced === true;
	const baseId = useId();
	const [act, setAct] = useState<LandingAct>('why');
	// Every close ends the visit: the next opening starts on Act 1.
	const close = () => {
		setAct('why');
		onClose();
	};

	// Lock the page behind the overlay; restore exactly what was there.
	useEffect(() => {
		if (!open) return undefined;
		const root = document.documentElement;
		const prev = root.style.overflow;
		root.style.overflow = 'hidden';
		return () => {
			root.style.overflow = prev;
		};
	}, [open]);

	const { onAddApis, onOpenSurface } = actions;
	const tourActions: LandingActions = {
		...actions,
		onCreateAgent: afterClose(close, actions.onCreateAgent),
		onAddApis: onAddApis && afterClose(close, onAddApis),
		onOpenSurface: onOpenSurface && afterClose(close, onOpenSurface),
		onOpenNotifications: afterClose(close, actions.onOpenNotifications ?? pressTopBarBell),
	};

	return (
		<Dialog
			open={open}
			onClose={close}
			size="full"
			title="The Jentic One tour"
			header={<LandingTabs baseId={baseId} act={act} onChange={setAct} />}
			dismissOnBackdrop
		>
			{open && (
				// A link inside the tour (the catalog, `?credentials=new`, Monitor) is a
				// CTA too: close the overlay so its target is not left underneath.
				<div
					data-testid="agents-tour"
					onClickCapture={(e) => {
						if ((e.target as HTMLElement).closest('a[href]')) close();
					}}
				>
					<AgentsLanding
						baseId={baseId}
						act={act}
						onActChange={setAct}
						actions={tourActions}
						reducedMotion={reduced}
					/>
				</div>
			)}
		</Dialog>
	);
}
