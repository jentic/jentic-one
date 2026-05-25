import { Copy, Check } from 'lucide-react';
import { Button } from './Button';
import { toast } from './toastStore';
import { useCopyToClipboard } from '@/hooks/useCopyToClipboard';
import { cn } from '@/lib/utils';

interface CopyButtonProps {
	value: string;
	/**
	 * Visible text rendered next to the icon. Omit for an icon-only
	 * button — see `ariaLabel` to customise the accessible name in
	 * that case.
	 */
	label?: string;
	/**
	 * Override the button's accessible name. Only used when `label`
	 * is omitted (icon-only mode); when a visible label is present
	 * the label itself names the button. Defaults to "Copy to
	 * clipboard".
	 */
	ariaLabel?: string;
	className?: string;
	/**
	 * Custom toast message shown after a successful copy. Set to `false`
	 * to opt out of the toast entirely (e.g. when the parent surface
	 * already provides its own success feedback). Defaults to a generic
	 * "Copied to clipboard" notification.
	 */
	toastMessage?: string | false;
	/** Visual size — matches the underlying `Button` size token. Defaults to `sm`. */
	size?: 'sm' | 'icon';
	/** Visual variant — matches the underlying `Button` variant. Defaults to `secondary`. */
	variant?: 'secondary' | 'ghost';
}

/**
 * Reusable "copy to clipboard" affordance.
 *
 * Visual design ported from `jentic-frontend-ui`'s `CopyButton`, but routed
 * through the in-app `toastStore` rather than `sonner` so we don't ship a
 * second toast system. Renders an inline check/clipboard icon (with optional
 * trailing label) and fires a transient toast on success.
 */
export function CopyButton({
	value,
	label,
	ariaLabel,
	className,
	toastMessage = 'Copied to clipboard',
	size = 'sm',
	variant = 'secondary',
}: CopyButtonProps) {
	const { copied, copy } = useCopyToClipboard();

	const handleClick = async () => {
		try {
			await copy(value);
			if (toastMessage !== false) {
				// Stable id so rapid double-clicks just refresh the toast
				// instead of stacking three of them on the screen.
				toast({
					id: 'copy-to-clipboard',
					title: toastMessage,
					variant: 'success',
					durationMs: 2000,
				});
			}
		} catch {
			toast({
				id: 'copy-to-clipboard',
				title: 'Could not copy to clipboard',
				variant: 'error',
			});
		}
	};

	return (
		<Button
			type="button"
			variant={variant}
			size={size}
			onClick={() => void handleClick()}
			className={cn('shrink-0', className)}
			aria-label={label ? undefined : (ariaLabel ?? 'Copy to clipboard')}
		>
			{copied ? (
				<>
					<Check className="text-success h-4 w-4" />
					{label ? 'Copied!' : null}
				</>
			) : (
				<>
					<Copy className="h-4 w-4" />
					{label ?? null}
				</>
			)}
		</Button>
	);
}
