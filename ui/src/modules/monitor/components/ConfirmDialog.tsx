/**
 * Module-local confirmation dialog for Monitor's one destructive action
 * (Cancel job). Thin wrapper over the shared `Dialog` primitive — kept inside
 * the module rather than importing another module's confirm dialog, per the
 * module-isolation convention.
 */
import { Button, Dialog } from '@/shared/ui';
import type { ReactNode } from 'react';

interface ConfirmDialogProps {
	open: boolean;
	title: string;
	body: ReactNode;
	confirmLabel: string;
	onConfirm: () => void;
	onClose: () => void;
	pending?: boolean;
	/** Destructive actions use the danger button; defaults to true. */
	destructive?: boolean;
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
}: ConfirmDialogProps) {
	return (
		<Dialog
			open={open}
			onClose={onClose}
			title={title}
			size="sm"
			footer={
				<>
					<Button variant="secondary" onClick={onClose} disabled={pending}>
						Keep job
					</Button>
					<Button
						variant={destructive ? 'danger' : 'primary'}
						onClick={onConfirm}
						loading={pending}
					>
						{confirmLabel}
					</Button>
				</>
			}
		>
			<div className="text-muted-foreground text-sm leading-relaxed">{body}</div>
		</Dialog>
	);
}
