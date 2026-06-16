import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2, X } from 'lucide-react';
import { CredentialFormFields } from './form/CredentialFormFields';
import { api } from '@/api/client';
import type { ApiOut } from '@/api/types';
import { Button } from '@/components/ui/Button';
import { LoadingState } from '@/components/ui/LoadingState';
import { SheetPrimitive } from '@/components/ui/SheetPrimitive';

/**
 * Right-side slide-over for editing an existing credential.
 *
 * The sheet is intentionally narrow: it only edits. New credentials go
 * through `AddCredentialDialog` (Phase 3) — putting "create" in here too
 * would either force a picker step inside a 480px-wide panel or mean the
 * sheet has two distinct shapes depending on `editId === null`. Neither
 * is worth the complexity. Hosts that need both surface the dialog
 * separately.
 *
 * Lifecycle:
 *   1. Host owns `editId` (typically via `useCredentialEditSheet`).
 *   2. We fetch the credential and its API in parallel — both queries
 *      are scoped to the editId so they're cheap and cache-friendly.
 *   3. While loading we render a centered LoadingState; while
 *      `editId === null` we render nothing (parent controls open state
 *      separately).
 *   4. On save the form calls `onSaved`; we call `onClose` so the host
 *      can drop the `?edit=` param.
 *
 * Sticky id pattern: when the host clears `editId` while the sheet
 * animates out, we keep rendering the previously-resolved credential
 * row so the body doesn't blank during the 300ms slide. Once
 * `onAfterClose` fires, the host typically clears the sticky.
 *
 * The sheet does NOT take over routing or query invalidation —
 * `CredentialFormFields` handles the create/update mutation and
 * invalidates the `['credentials']` query already, the same way it
 * does on the legacy form page.
 */
export interface CredentialEditSheetProps {
	/**
	 * The credential being edited. Also drives the queries below.
	 * Pass `null` to render nothing (use this for closed state).
	 *
	 * Hosts that use the sticky pattern should pass the sticky id
	 * (not the live URL param) so the body stays mounted through the
	 * close animation.
	 */
	credentialId: string | null;
	/** Whether the sheet should be visually open. */
	open: boolean;
	onClose: () => void;
	onAfterClose?: () => void;
}

export function CredentialEditSheet({
	credentialId,
	open,
	onClose,
	onAfterClose,
}: CredentialEditSheetProps) {
	// Heading anchor — used by SheetPrimitive's aria-labelledby so
	// screen readers announce "Edit credential — Foo Bar API" rather
	// than "dialog".
	const headingId = 'credential-edit-sheet-title';
	// Initial focus target for the sheet — the close button. The
	// label input would be a tempting alternative, but it auto-selects
	// on focus and would clobber the existing label visually for
	// users with text-cursor visualisers. Close is unambiguous.
	const closeButtonRef = useRef<HTMLButtonElement | null>(null);

	const { data: existing, isLoading: loadingCred } = useQuery({
		queryKey: ['credential', credentialId],
		queryFn: () => api.getCredential(credentialId!),
		enabled: !!credentialId,
	});

	const { data: existingApi, isLoading: loadingApi } = useQuery({
		queryKey: ['api', existing?.api_id],
		queryFn: () => api.getApi(existing!.api_id!),
		enabled: !!existing?.api_id,
	});

	// Re-focus the close button each time the sheet (re-)opens, so
	// keyboard users get a stable focus anchor even when the
	// credential body is hot-swapped (clicking Edit on a different
	// row while the sheet is open).
	useEffect(() => {
		if (open) closeButtonRef.current?.focus();
	}, [open, credentialId]);

	return (
		<SheetPrimitive
			open={open}
			onClose={onClose}
			onAfterClose={onAfterClose}
			side="right"
			ariaLabelledBy={headingId}
			initialFocus={closeButtonRef}
		>
			<div className="flex h-full flex-col">
				<header className="border-border flex items-center justify-between gap-2 border-b px-5 py-3">
					<div className="min-w-0">
						<h2 id={headingId} className="text-foreground text-base font-semibold">
							Edit credential
						</h2>
						{existing?.label && (
							<p className="text-muted-foreground truncate text-xs">
								{existing.label}
							</p>
						)}
					</div>
					<Button
						ref={closeButtonRef}
						variant="ghost"
						size="sm"
						aria-label="Close"
						onClick={onClose}
						className="text-muted-foreground hover:text-foreground"
					>
						<X className="h-4 w-4" />
					</Button>
				</header>

				<div className="flex-1 overflow-hidden">
					{credentialId && (loadingCred || loadingApi || !existingApi) && (
						<div className="px-5 py-4">
							<LoadingState
								message={loadingCred ? 'Loading credential…' : 'Loading API…'}
								icon={<Loader2 className="h-5 w-5 animate-spin" />}
							/>
						</div>
					)}
					{credentialId && existing && existingApi && (
						<div className="h-full">
							<CredentialFormFields
								key={credentialId}
								selectedApi={existingApi as ApiOut}
								editId={credentialId}
								existing={existing}
								onBack={onClose}
								onSaved={onClose}
								hideApiSummary
								layout="sheet"
							/>
						</div>
					)}
				</div>
			</div>
		</SheetPrimitive>
	);
}
