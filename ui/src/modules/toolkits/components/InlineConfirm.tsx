import {
	cloneElement,
	isValidElement,
	useEffect,
	useId,
	useRef,
	useState,
	type MouseEvent,
	type ReactElement,
	type ReactNode,
} from 'react';
import { Button } from '@/shared/ui';

/**
 * Inline two-step confirm for destructive row actions (revoke key, unbind
 * credential, revoke agent). Replaces mini's shared `ConfirmInline` with an
 * in-module equivalent: the trigger arms an inline "are you sure?" group, and
 * confirming fires `onConfirm`. Focus moves to the confirm button on arm so the
 * destructive action is reachable from the keyboard.
 *
 * `children` must be a single interactive element (typically a `<Button>`); we
 * clone it and inject the arming `onClick` so there's no extra interactive
 * wrapper (which would trip axe's `nested-interactive` rule).
 */
export interface InlineConfirmProps {
	onConfirm: () => void;
	message: string;
	confirmLabel: string;
	children: ReactElement<{ onClick?: (e: MouseEvent) => void; disabled?: boolean }>;
	disabled?: boolean;
}

export function InlineConfirm({
	onConfirm,
	message,
	confirmLabel,
	children,
	disabled,
}: InlineConfirmProps) {
	const [armed, setArmed] = useState(false);
	const groupId = useId();
	const confirmRef = useRef<HTMLButtonElement>(null);

	useEffect(() => {
		if (armed) confirmRef.current?.focus();
	}, [armed]);

	if (!armed) {
		const trigger: ReactNode = isValidElement(children)
			? cloneElement(children, {
					disabled: disabled || children.props.disabled,
					onClick: (e: MouseEvent) => {
						children.props.onClick?.(e);
						if (!disabled) setArmed(true);
					},
				})
			: children;
		return <>{trigger}</>;
	}

	return (
		<span
			id={groupId}
			role="group"
			aria-label={message}
			className="border-danger/30 bg-danger/5 inline-flex items-center gap-2 rounded-md border px-2 py-1"
		>
			<span className="text-muted-foreground text-xs">{message}</span>
			<Button
				ref={confirmRef}
				variant="danger"
				size="sm"
				className="px-2 py-0.5 text-xs"
				disabled={disabled}
				onClick={() => {
					onConfirm();
					setArmed(false);
				}}
			>
				{confirmLabel}
			</Button>
			<Button
				variant="ghost"
				size="sm"
				className="px-2 py-0.5 text-xs"
				onClick={() => setArmed(false)}
			>
				Cancel
			</Button>
		</span>
	);
}
