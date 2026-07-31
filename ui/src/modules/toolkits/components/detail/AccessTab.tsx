import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
	AlertTriangle,
	ChevronDown,
	Edit2,
	Key,
	Link as LinkIcon,
	ShieldCheck,
	Unlink,
} from 'lucide-react';
import { Button, DetailSection, EmptyRow, ErrorAlert } from '@/shared/ui';
import { OperationsSummary } from '@/shared/app';
import { apiIdentityTuple, toolkitCredDisplayName } from '@/shared/lib';
import { useToolkitBindings, useUnbindCredential } from '@/modules/toolkits/api';
import { BindCredentialDialog } from '@/modules/toolkits/components/BindCredentialDialog';
import { CredentialPermissionEditor } from '@/modules/toolkits/components/CredentialPermissionEditor';
import { InlineConfirm } from '@/modules/toolkits/components/InlineConfirm';
import {
	panelMotion,
	rowMotion,
	toDisplayRules,
} from '@/modules/toolkits/components/detail/shared';

/**
 * Access tab — what this toolkit is allowed to call: credential bindings and
 * their per-binding permission rules (the broker's allow/deny list). Each
 * binding renders its grant through the same `OperationsSummary` the
 * access-request review cards use, so "what can this credential do" reads
 * identically at review time and on the live binding.
 */
export function AccessTab({
	toolkitId,
	agentless = false,
	onLinkAgent,
}: {
	toolkitId: string;
	/** No linked agent and no API key — enables the post-bind link prompt. */
	agentless?: boolean;
	/** Switch to Overview AND open the link-agent picker there (host-wired). */
	onLinkAgent?: () => void;
}) {
	const { data: bindings = [], isError: bindingsError } = useToolkitBindings(toolkitId);
	const unbindCredential = useUnbindCredential(toolkitId);

	const [bindOpen, setBindOpen] = useState(false);
	const [editingPermForCred, setEditingPermForCred] = useState<string | null>(null);

	const boundIds = new Set(bindings.map((b) => b.credential_id));

	return (
		<>
			<DetailSection
				title={`Bound credentials (${bindings.length})`}
				icon={<ShieldCheck className="h-4 w-4" />}
				action={{
					label: (
						<>
							<LinkIcon className="h-4 w-4" /> Bind credential
						</>
					),
					onClick: () => setBindOpen(true),
				}}
			>
				{bindingsError && <ErrorAlert message="Failed to load bound credentials." />}
				{!bindingsError && bindings.length === 0 ? (
					<EmptyRow icon={<Key />}>
						No credentials bound. Bind a credential to grant this toolkit API access.
					</EmptyRow>
				) : (
					<AnimatePresence initial={false}>
						{bindings.map((cred) => {
							const displayRules = toDisplayRules(cred.permissions);
							// Heading = the user's own credential label when set, so a
							// renamed credential leads with that name (matching the
							// credentials page). Fall back to the friendly API name, then
							// the credential id, so the row never renders blank.
							const heading =
								cred.label || toolkitCredDisplayName(cred) || cred.credential_id;
							// Muted technical subtitle: the raw vendor/name tuple, via the
							// shared helper so a tuple-shaped `api_name` doesn't render the
							// vendor twice. `credential_id` is the last-resort fallback for
							// telling identically labelled rows apart (matching Overview
							// and the picker) — unless the heading already IS the id, in
							// which case repeating it is pure noise.
							const subtitle =
								apiIdentityTuple({
									vendor: cred.api_vendor,
									name: cred.api_name,
								}) || (heading === cred.credential_id ? '' : cred.credential_id);
							return (
								<motion.div
									key={cred.credential_id}
									{...rowMotion}
									layout
									data-testid="binding-row"
									className="bg-muted/30 border-border/60 hover:border-border overflow-hidden rounded-lg border transition-colors"
								>
									<div className="flex flex-wrap items-center gap-3 px-4 py-3">
										<div className="min-w-0 flex-1 basis-40">
											<span className="text-foreground text-sm font-medium">
												{heading}
											</span>
											{subtitle && (
												<p className="text-muted-foreground truncate font-mono text-xs">
													{subtitle}
												</p>
											)}
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
												<Edit2 className="h-3 w-3" /> Edit rules
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
												message="Unbind this credential?"
												confirmLabel="Unbind"
											>
												<Button
													variant="danger"
													size="sm"
													className="inline-flex items-center gap-1 px-2 py-1 text-xs"
												>
													<Unlink className="h-3 w-3" /> Unbind
												</Button>
											</InlineConfirm>
										</div>
										{/* The grant, in the platform's one operations grammar
										    (effect chips + bounded preview + full-view dialog).
										    Zero agent rules ⇒ the broker default-denies; say so
										    in the same voice the Overview summary uses. */}
										<div className="w-full">
											{displayRules.length > 0 ? (
												<OperationsSummary
													rules={displayRules}
													targetLabel={cred.label ?? cred.credential_id}
												/>
											) : (
												<p
													className="text-warning flex items-center gap-1.5 text-xs"
													data-testid="binding-no-rules"
												>
													<AlertTriangle
														className="h-3.5 w-3.5 shrink-0"
														aria-hidden="true"
													/>
													All operations blocked — add an allow rule to
													grant access.
												</p>
											)}
										</div>
									</div>
									{/* Bind-time warnings from the API (BindingWarningSchema)
									    — e.g. zero rules ⇒ broker default-denies. Rendered
									    verbatim; the backend message carries the recovery hint. */}
									{(cred.warnings ?? []).length > 0 && (
										<div className="border-warning/30 bg-warning/5 space-y-1 border-t px-4 py-2.5">
											{(cred.warnings ?? []).map((warning) => (
												<p
													key={warning.code}
													className="text-warning flex items-start gap-1.5 text-xs"
													data-testid="binding-warning"
												>
													<AlertTriangle
														className="mt-0.5 h-3.5 w-3.5 shrink-0"
														aria-hidden="true"
													/>
													{warning.message}
												</p>
											))}
										</div>
									)}
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

			{/* Two-step bind wizard (mounted once — its draft survives dismissal). */}
			<BindCredentialDialog
				toolkitId={toolkitId}
				open={bindOpen}
				onClose={() => setBindOpen(false)}
				boundIds={boundIds}
				agentless={agentless}
				onLinkAgent={onLinkAgent}
			/>
		</>
	);
}
