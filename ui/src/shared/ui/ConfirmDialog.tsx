/**
 * ConfirmDialog — a plain confirmation step over the shared `Dialog` primitive
 * for actions that need no input (disable, archive, cancel a job, a
 * destructive re-import…). Destructive by default: the confirm button uses the
 * danger variant unless `destructive={false}`.
 */
import { Button } from '@/shared/ui/Button';
import { Dialog } from '@/shared/ui/Dialog';
import { useId, type ReactNode } from 'react';

export interface ConfirmDialogProps {
	open: boolean;
	title: ReactNode;
	body: ReactNode;
	confirmLabel: string;
	onConfirm: () => void;
	onClose: () => void;
	pending?: boolean;
	/** Destructive actions use the danger button; defaults to true. */
	destructive?: boolean;
	/** Label of the dismiss button; defaults to "Cancel". */
	cancelLabel?: string;
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
	cancelLabel = 'Cancel',
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
						{cancelLabel}
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
