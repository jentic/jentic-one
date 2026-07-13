import { Button, Dialog } from '@/shared/ui';

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
}

/**
 * Plain confirmation dialog for lifecycle actions that need no input —
 * Disable (killswitch) and Archive (terminal). Deny has its own dialog because
 * it requires a reason.
 */
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
						Cancel
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
