/**
 * ConfirmDialog — a thin workspace-local confirm dialog over the shared
 * `Dialog` primitive (modeled on the agents module's ConfirmDialog).
 *
 * Used to gate the destructive overlay-origin re-import: re-importing an
 * overlay-origin API deprecates its confirmed overlay(s) and adopts the
 * upstream spec, which can't be undone automatically — so the action is routed
 * through this confirm step. Destructive by default.
 */
import { Button, Dialog } from '@/shared/ui';
import { useId } from 'react';

interface ConfirmDialogProps {
	open: boolean;
	title: string;
	body: React.ReactNode;
	confirmLabel: string;
	onConfirm: () => void;
	onClose: () => void;
	pending?: boolean;
	/** Destructive actions use the danger button; defaults to true. */
	destructive?: boolean;
	/** Applied to the confirm button (e.g. a `data-testid`). */
	confirmTestId?: string;
}

export function ConfirmDialog({
	open,
	title,
	body,
	confirmLabel,
	onConfirm,
	onClose,
	pending,
	destructive = true,
	confirmTestId,
}: ConfirmDialogProps) {
	const bodyId = useId();
	return (
		<Dialog
			open={open}
			onClose={onClose}
			title={title}
			size="sm"
			describedById={bodyId}
			footer={
				<>
					<Button variant="secondary" onClick={onClose} disabled={pending}>
						Cancel
					</Button>
					<Button
						variant={destructive ? 'danger' : 'primary'}
						onClick={onConfirm}
						loading={pending}
						data-testid={confirmTestId}
					>
						{confirmLabel}
					</Button>
				</>
			}
		>
			<div id={bodyId} className="text-muted-foreground text-sm leading-relaxed">
				{body}
			</div>
		</Dialog>
	);
}
