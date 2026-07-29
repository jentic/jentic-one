import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { AlertTriangle, ChevronDown, Edit2, Key, Link as LinkIcon, Unlink } from 'lucide-react';
import { AppLink, Button, Dialog, ErrorAlert } from '@/shared/ui';
import { useBindCredential, useToolkitBindings, useUnbindCredential } from '@/modules/toolkits/api';
import { CredentialPermissionEditor } from '@/modules/toolkits/components/CredentialPermissionEditor';
import { CredentialPicker } from '@/modules/toolkits/components/CredentialPicker';
import { InlineConfirm } from '@/modules/toolkits/components/InlineConfirm';
import {
	DetailSection,
	EmptyRow,
	panelMotion,
	rowMotion,
} from '@/modules/toolkits/components/detail/shared';
import { ROUTES } from '@/shared/app/routes';

/**
 * Access tab — what this toolkit is allowed to call: credential bindings and
 * their per-binding permission rules (the broker's allow/deny list).
 */
export function AccessTab({ toolkitId }: { toolkitId: string }) {
	const { data: bindings = [], isError: bindingsError } = useToolkitBindings(toolkitId);
	const bindCredential = useBindCredential(toolkitId);
	const unbindCredential = useUnbindCredential(toolkitId);

	const [bindOpen, setBindOpen] = useState(false);
	const [editingPermForCred, setEditingPermForCred] = useState<string | null>(null);

	const boundIds = new Set(bindings.map((b) => b.credential_id));

	const submitBind = (credentialId: string) => {
		if (!credentialId) return;
		bindCredential.mutate(
			{ credential_id: credentialId },
			{
				onSuccess: () => {
					setBindOpen(false);
				},
			},
		);
	};

	return (
		<>
			<DetailSection
				title={`Bound Credentials (${bindings.length})`}
				action={{
					label: (
						<>
							<LinkIcon className="h-4 w-4" /> Bind API
						</>
					),
					onClick: () => setBindOpen(true),
				}}
			>
				{bindingsError && <ErrorAlert message="Failed to load bound credentials." />}
				{!bindingsError && bindings.length === 0 ? (
					<EmptyRow icon={<Key />}>
						No credentials bound. Bind credentials to grant this toolkit API access.
					</EmptyRow>
				) : (
					<AnimatePresence initial={false}>
						{bindings.map((cred) => {
							const agentRules = (cred.permissions ?? []).filter((r) => !r._system);
							return (
								<motion.div
									key={cred.credential_id}
									{...rowMotion}
									layout
									className="bg-muted/30 border-border/60 hover:border-border overflow-hidden rounded-lg border transition-colors"
								>
									<div className="flex flex-wrap items-center gap-3 px-4 py-3">
										<div className="min-w-0 flex-1 basis-40">
											<span className="text-foreground text-sm font-medium">
												{cred.label ?? cred.credential_id}
											</span>
											{(cred.api_name || cred.api_vendor) && (
												<p className="text-muted-foreground truncate font-mono text-xs">
													{cred.api_name ?? cred.api_vendor}
												</p>
											)}
											<p className="text-muted-foreground mt-0.5 flex items-center gap-1 text-xs">
												{agentRules.length === 0 ? (
													<span
														className="text-warning inline-flex items-center gap-1"
														title="All operations blocked — no allow rules defined"
													>
														<AlertTriangle className="h-3 w-3" />
														No rules — all ops blocked
													</span>
												) : (
													<>
														{agentRules.length} agent rule(s) + system
														safety
													</>
												)}
											</p>
										</div>
										<div className="ml-auto flex w-full shrink-0 items-center justify-end gap-1.5 sm:w-auto">
											<Button
												variant="secondary"
												size="sm"
												onClick={() =>
													setEditingPermForCred(
														editingPermForCred === cred.credential_id
															? null
															: cred.credential_id,
													)
												}
												aria-expanded={
													editingPermForCred === cred.credential_id
												}
												className="inline-flex items-center gap-1 px-2 py-1 text-xs"
											>
												<Edit2 className="h-3 w-3" /> Permissions
												<motion.span
													animate={{
														rotate:
															editingPermForCred ===
															cred.credential_id
																? 180
																: 0,
													}}
													transition={{ duration: 0.18 }}
													className="flex"
												>
													<ChevronDown className="h-3 w-3" />
												</motion.span>
											</Button>
											<InlineConfirm
												onConfirm={() =>
													unbindCredential.mutate(cred.credential_id)
												}
												message="Unbind this API?"
												confirmLabel="Unbind API"
											>
												<Button
													variant="danger"
													size="sm"
													className="inline-flex items-center gap-1 px-2 py-1 text-xs"
												>
													<Unlink className="h-3 w-3" /> Unbind API
												</Button>
											</InlineConfirm>
										</div>
									</div>
									<AnimatePresence initial={false}>
										{editingPermForCred === cred.credential_id && (
											<motion.div
												{...panelMotion}
												className="overflow-hidden"
											>
												<CredentialPermissionEditor
													toolkitId={toolkitId}
													credentialId={cred.credential_id}
													credentialLabel={
														cred.label ?? cred.credential_id
													}
													initialRules={cred.permissions ?? []}
													onClose={() => setEditingPermForCred(null)}
												/>
											</motion.div>
										)}
									</AnimatePresence>
								</motion.div>
							);
						})}
					</AnimatePresence>
				)}
			</DetailSection>

			{/* Bind-existing dialog — stateless picker, selection is the commit. */}
			<Dialog
				open={bindOpen}
				onClose={() => setBindOpen(false)}
				title="Bind API"
				size="sm"
				footer={
					<Button variant="secondary" onClick={() => setBindOpen(false)}>
						Cancel
					</Button>
				}
			>
				<div className="space-y-3">
					<p className="text-muted-foreground text-sm">
						Pick a credential to bind to this toolkit. Manage credentials on the{' '}
						<AppLink href={ROUTES.credentials} className="text-primary font-medium">
							Credentials
						</AppLink>{' '}
						page.
					</p>
					<CredentialPicker
						boundIds={boundIds}
						onSelect={submitBind}
						pending={bindCredential.isPending}
						enabled={bindOpen}
					/>
				</div>
			</Dialog>
		</>
	);
}
