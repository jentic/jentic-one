/**
 * UnappliedChoicesDialog — By action's choices are staged until Apply, so
 * anything that would leave them behind (switching to By path, adding the
 * API, closing the panel) asks first: Apply them, Discard them, or keep
 * editing. Escape and the backdrop keep editing; nothing is dropped silently.
 */
import { useRef, useState, type MutableRefObject, type ReactNode } from 'react';
import { Button, Dialog } from '@/shared/ui';
import type { PermissionRule } from '@/shared/credentials/api/vendors-types';

export interface UnappliedChoicesDialogProps {
	open: boolean;
	/** How many action choices are staged. */
	count: number;
	/** What happens next, for the body ("before switching to By path"). */
	context?: string;
	onApply: () => void;
	onDiscard: () => void;
	/** Keep editing: close the dialog and do nothing else. */
	onClose: () => void;
}

export function UnappliedChoicesDialog({
	open,
	count,
	context,
	onApply,
	onDiscard,
	onClose,
}: UnappliedChoicesDialogProps) {
	const changes = `${count.toLocaleString()} ${count === 1 ? 'change' : 'changes'}`;
	return (
		<Dialog
			open={open}
			onClose={onClose}
			size="sm"
			title={`Apply ${changes} by action?`}
			footer={
				<>
					<Button variant="ghost" onClick={onClose}>
						Keep editing
					</Button>
					<Button variant="secondary" onClick={onDiscard}>
						Discard
					</Button>
					<Button onClick={onApply}>Apply</Button>
				</>
			}
		>
			<p className="text-muted-foreground text-sm leading-relaxed">
				{`The action choices you made aren't rules yet${context ? ` — apply them ${context}, or discard them` : ''}.`}
			</p>
		</Dialog>
	);
}

/** What a host needs to settle staged By action choices before it moves on. */
export interface UnappliedChoices {
	count: number;
	/** Apply the staged choices; returns the custom rules they make. */
	apply: () => PermissionRule[];
	discard: () => void;
}

/** A host's side of the staged choices: hand `stagedRef` and `onStagedChange`
 * to the access step, `settle` before anything that would leave them behind,
 * and render `dialog`. */
export interface UnappliedChoicesHost {
	stagedRef: MutableRefObject<UnappliedChoices | null>;
	/** How many choices are staged. */
	staged: number;
	onStagedChange: (count: number) => void;
	/** Run `then` now, or once the operator applies (`then` gets the rules the
	 * choices make) or discards (`then` gets null) what's staged. */
	settle: (context: string, then: (applied: PermissionRule[] | null) => void) => void;
	/** The settle dialog is open. */
	settling: boolean;
	dialog: ReactNode;
}

export function useUnappliedChoices(): UnappliedChoicesHost {
	const stagedRef = useRef<UnappliedChoices | null>(null);
	const [staged, setStaged] = useState(0);
	const [pending, setPending] = useState<null | {
		context: string;
		then: (applied: PermissionRule[] | null) => void;
	}>(null);
	const finish = (applied: PermissionRule[] | null): void => {
		setPending(null);
		pending?.then(applied);
	};
	return {
		stagedRef,
		staged,
		onStagedChange: setStaged,
		settle: (context, then) => {
			if (stagedRef.current) setPending({ context, then });
			else then(null);
		},
		settling: pending != null,
		dialog: (
			<UnappliedChoicesDialog
				open={pending != null}
				count={staged}
				context={pending?.context}
				onApply={(): void => finish(stagedRef.current?.apply() ?? null)}
				onDiscard={(): void => {
					stagedRef.current?.discard();
					finish(null);
				}}
				onClose={(): void => setPending(null)}
			/>
		),
	};
}
