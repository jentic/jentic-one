/**
 * NoExpiryConfirmDialog — the warning before a binding is created, or renewed,
 * with no expiry, in the spirit of a personal access token made without an
 * expiration: the agent keeps this access until someone removes it.
 * "Set an expiry" is the default action (focused, and what
 * Escape or the backdrop does): it closes the warning and puts focus on the
 * duration control. Nothing is saved until the operator confirms.
 *
 * Callers hold the pending action with {@link useNoExpiryConfirm}: `request`
 * runs it at once when the choice sets an expiry, and parks it behind this
 * dialog when it doesn't.
 */
import {
	useCallback,
	useEffect,
	useId,
	useRef,
	useState,
	type ReactNode,
	type RefObject,
} from 'react';
import { Button, Dialog } from '@/shared/ui';

export interface NoExpiryConfirmDialogProps {
	open: boolean;
	/** Creating access, or clearing the expiry of access that exists. */
	kind?: 'create' | 'renew';
	/** Who keeps the access — an agent's name, or "These agents". */
	subject?: ReactNode;
	/** The secondary button that goes ahead without an expiry. */
	confirmLabel?: string;
	onConfirm: () => void;
	/** "Set an expiry", Escape or the backdrop: back to choosing. */
	onCancel: () => void;
	/** The duration control's dropdown, focused once the warning closes. */
	fieldRef?: RefObject<HTMLSelectElement | null>;
	pending?: boolean;
}

export function NoExpiryConfirmDialog({
	open,
	kind = 'create',
	subject,
	confirmLabel,
	onConfirm,
	onCancel,
	fieldRef,
	pending = false,
}: NoExpiryConfirmDialogProps) {
	const bodyId = useId();
	const setExpiryRef = useRef<HTMLButtonElement>(null);
	// The dialog opens in its own effect (a child's runs first), so by now it is
	// showing and the default action can take focus from the first button.
	useEffect(() => {
		if (open) setExpiryRef.current?.focus();
	}, [open]);

	const setExpiry = (): void => {
		onCancel();
		// After the dialog closes and hands focus back to whatever opened it.
		requestAnimationFrame(() => fieldRef?.current?.focus());
	};

	// Mounted only while asking: a stateless confirm, like the other confirms.
	if (!open) return null;
	return (
		<Dialog
			open={open}
			onClose={setExpiry}
			size="sm"
			describedById={bodyId}
			title={kind === 'renew' ? 'Remove the expiry?' : 'Give access without an expiry?'}
			footer={
				<>
					<Button
						variant="secondary"
						onClick={onConfirm}
						loading={pending}
						data-testid="no-expiry-confirm"
					>
						{confirmLabel ??
							(kind === 'renew' ? 'Remove expiry' : 'Create without expiry')}
					</Button>
					<Button
						ref={setExpiryRef}
						onClick={setExpiry}
						disabled={pending}
						data-testid="no-expiry-set-expiry"
					>
						Set an expiry
					</Button>
				</>
			}
		>
			<div
				id={bodyId}
				className="text-muted-foreground space-y-2 text-sm leading-relaxed"
				data-testid="no-expiry-warning"
			>
				<p>{subject ?? 'The agent'} will keep this access until someone removes it.</p>
				<p>
					Setting an expiry is recommended, so access ends on its own once it is no longer
					needed. It can be renewed before or after it ends.
				</p>
			</div>
		</Dialog>
	);
}

/**
 * The action waiting on the "No expiry" warning. `request(withoutExpiry, run)`
 * runs `run` now, or holds it until {@link NoExpiryConfirmDialog} is confirmed;
 * spread `dialogProps` onto the dialog.
 */
export function useNoExpiryConfirm(): {
	request: (withoutExpiry: boolean, run: () => void) => void;
	dialogProps: Pick<NoExpiryConfirmDialogProps, 'open' | 'onConfirm' | 'onCancel'>;
} {
	const held = useRef<(() => void) | null>(null);
	const [open, setOpen] = useState(false);
	const request = useCallback((withoutExpiry: boolean, run: () => void): void => {
		if (!withoutExpiry) {
			run();
			return;
		}
		held.current = run;
		setOpen(true);
	}, []);
	const onConfirm = useCallback((): void => {
		const run = held.current;
		held.current = null;
		setOpen(false);
		run?.();
	}, []);
	const onCancel = useCallback((): void => {
		held.current = null;
		setOpen(false);
	}, []);
	return { request, dialogProps: { open, onConfirm, onCancel } };
}
