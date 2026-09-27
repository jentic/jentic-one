/**
 * ApiActionsMenu — the API detail header's overflow ("More actions") menu.
 *
 * Holds the actions an operator reaches for rarely: copying the API's
 * identity triple, and removing the API. Removal is destructive for
 * everyone in a shared workspace (every agent granted the API loses it), so it
 * sits behind the menu and its cascade confirm rather than as a red button
 * beside "View spec".
 */
import { useRef, useState } from 'react';
import { Copy, MoreHorizontal, Trash2 } from 'lucide-react';
import {
	Button,
	MenuPanel,
	MenuSeparator,
	menuItemClass,
	toast,
	useDismissable,
} from '@/shared/ui';
import { cn } from '@/shared/lib/utils';

export interface ApiActionsMenuProps {
	/** The `vendor/name/version` identity, copied verbatim. */
	apiId: string;
	/** The API's display title, for the accessible names. */
	title: string;
	disabled?: boolean;
	onRemove: () => void;
}

export function ApiActionsMenu({ apiId, title, disabled, onRemove }: ApiActionsMenuProps) {
	const [open, setOpen] = useState(false);
	const triggerRef = useRef<HTMLButtonElement | null>(null);
	const containerRef = useDismissable<HTMLDivElement>(open, () => setOpen(false));

	function close() {
		setOpen(false);
		triggerRef.current?.focus();
	}

	async function copyId() {
		close();
		try {
			await navigator.clipboard.writeText(apiId);
			toast({ variant: 'success', title: 'API id copied', description: apiId });
		} catch {
			toast({ variant: 'error', title: 'Could not copy the API id' });
		}
	}

	return (
		<div ref={containerRef} className="relative">
			<Button
				ref={triggerRef}
				variant="secondary"
				size="sm"
				onClick={() => setOpen((v) => !v)}
				disabled={disabled}
				aria-haspopup="menu"
				aria-expanded={open}
				aria-label={`More actions for ${title}`}
				data-testid="api-actions-menu"
			>
				<MoreHorizontal size={14} aria-hidden="true" />
			</Button>
			{open ? (
				<MenuPanel align="right" className="w-52">
					<button
						type="button"
						role="menuitem"
						onClick={copyId}
						className={menuItemClass()}
					>
						<Copy className="h-3.5 w-3.5" aria-hidden="true" />
						Copy API id
					</button>
					<MenuSeparator />
					<button
						type="button"
						role="menuitem"
						onClick={() => {
							close();
							onRemove();
						}}
						className={cn(menuItemClass(), 'text-danger hover:text-danger')}
						data-testid="remove-api"
					>
						<Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
						Remove API…
					</button>
				</MenuPanel>
			) : null}
		</div>
	);
}
