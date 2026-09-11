import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Ban, Check, Globe, Key, KeyRound, Pencil, X } from 'lucide-react';
import { Badge, Button, DetailSection, EmptyRow, ErrorAlert, Input, AppLink } from '@/shared/ui';
import { useRevokeKey, useToolkitKeys, useUpdateKey } from '@/modules/toolkits/api';
import { InlineConfirm } from '@/modules/toolkits/components/InlineConfirm';
import { rowMotion } from '@/modules/toolkits/components/detail/shared';
import { timeAgo } from '@/modules/toolkits/lib/time';
import { ROUTES } from '@/shared/app/routes';
import type { ToolkitKey } from '@/modules/toolkits/api/types';

/**
 * Keys tab — static toolkit API keys, now a legacy surface. Issuing NEW keys
 * is retired server-side (`POST /toolkits/{id}/keys` → 410
 * `toolkit_keys_retired`), so the tab offers no create affordance; a notice
 * points callers at service accounts (`sak_` keys) instead. Existing keys keep
 * working and remain manageable: label rename (inline pencil), the
 * `allowed_ips` chip, revoke.
 */

function KeyRow({
	toolkitKey: key,
	onRevoke,
	onRename,
	renamePending,
}: {
	toolkitKey: ToolkitKey;
	onRevoke: () => void;
	onRename: (label: string | null) => void;
	renamePending: boolean;
}) {
	const [editing, setEditing] = useState(false);
	const [draft, setDraft] = useState(key.label ?? '');

	const startEdit = () => {
		setDraft(key.label ?? '');
		setEditing(true);
	};
	const saveEdit = () => {
		onRename(draft.trim() || null);
		setEditing(false);
	};

	return (
		<motion.div
			{...rowMotion}
			layout
			data-testid="key-row"
			className="bg-muted/30 border-border/60 hover:border-border flex flex-wrap items-center gap-3 overflow-hidden rounded-lg border p-3 transition-colors"
		>
			<div className="bg-accent-yellow/10 text-accent-yellow flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
				<Key className="h-4 w-4" />
			</div>
			<div className="min-w-0 flex-1 basis-40">
				<div className="flex flex-wrap items-center gap-2">
					{editing ? (
						<span className="flex items-center gap-1">
							<Input
								value={draft}
								onChange={(e) => setDraft(e.target.value)}
								aria-label="Key label"
								size="sm"
								autoFocus
								onKeyDown={(e) => {
									if (e.key === 'Enter') saveEdit();
									if (e.key === 'Escape') setEditing(false);
								}}
							/>
							<Button
								variant="ghost"
								size="icon"
								onClick={saveEdit}
								loading={renamePending}
								aria-label="Save label"
							>
								<Check className="h-3.5 w-3.5" />
							</Button>
							<Button
								variant="ghost"
								size="icon"
								onClick={() => setEditing(false)}
								aria-label="Cancel rename"
							>
								<X className="h-3.5 w-3.5" />
							</Button>
						</span>
					) : (
						<>
							<span className="text-foreground truncate text-sm font-medium">
								{key.label || 'Unnamed key'}
							</span>
							{!key.revoked && (
								<Button
									variant="ghost"
									size="icon"
									onClick={startEdit}
									aria-label="Rename key"
									// Compact but NOT shrunken: a fixed 28px hit target with
									// centred padding — the old h-5 override clipped the
									// button's own p-2 and rendered a squashed 20px stub.
									className="h-7 w-7 p-0"
								>
									<Pencil className="h-3.5 w-3.5" />
								</Button>
							)}
						</>
					)}
					<code className="text-muted-foreground font-mono text-xs">
						{key.key_preview}
					</code>
					{key.revoked && <Badge variant="danger">Revoked</Badge>}
					{(key.allowed_ips ?? []).length > 0 && (
						<span
							className="border-border bg-card text-muted-foreground inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-[10px]"
							title={`Only callable from: ${(key.allowed_ips ?? []).join(', ')}`}
						>
							<Globe className="h-3 w-3" aria-hidden="true" />
							{(key.allowed_ips ?? []).join(', ')}
						</span>
					)}
				</div>
				<p className="text-muted-foreground truncate text-xs">
					Created {new Date(key.created_at).toLocaleString()}
					{key.last_used_at
						? ` · last used ${timeAgo(Date.parse(key.last_used_at))}`
						: ' · never used'}
				</p>
			</div>
			{!key.revoked && (
				<div className="ml-auto w-full sm:w-auto">
					<InlineConfirm
						onConfirm={onRevoke}
						message="Revoke this key?"
						confirmLabel="Revoke"
					>
						<Button variant="danger" size="sm" className="px-2 py-1 text-xs">
							Revoke
						</Button>
					</InlineConfirm>
				</div>
			)}
		</motion.div>
	);
}

export function KeysTab({ toolkitId, suspended }: { toolkitId: string; suspended: boolean }) {
	const { data: keys = [], isError: keysError } = useToolkitKeys(toolkitId);
	const revokeKey = useRevokeKey(toolkitId);
	const updateKey = useUpdateKey(toolkitId);

	return (
		<DetailSection
			title={`API keys (${keys.length})`}
			icon={<Key className="h-4 w-4" />}
			danger={suspended}
			titleExtra={
				suspended ? (
					<span className="bg-danger/15 text-danger border-danger/30 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-mono text-xs">
						<Ban className="h-3 w-3" />
						Keys blocked
					</span>
				) : undefined
			}
		>
			{/* Retirement notice — same inline info-banner grammar as the detail
			    page's status banners (icon chip + heading + body, role="status"). */}
			<div
				className="border-border/60 bg-muted/30 flex items-start gap-3 rounded-lg border p-3"
				role="status"
				data-testid="toolkit-keys-retired-notice"
			>
				<div className="bg-accent-yellow/10 text-accent-yellow flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
					<KeyRound className="h-4 w-4" />
				</div>
				<div className="min-w-0 flex-1">
					<p className="text-foreground text-sm font-medium">
						New toolkit keys are retired
					</p>
					<p className="text-muted-foreground mt-0.5 text-xs">
						This toolkit can no longer issue keys. Register a{' '}
						<AppLink
							href={ROUTES.agents}
							className="text-primary font-medium hover:underline"
						>
							service account
						</AppLink>{' '}
						and use its <code className="font-mono">sak_</code> API key instead.
						Existing keys below keep working and can still be renamed or revoked.
					</p>
				</div>
			</div>
			{keysError && <ErrorAlert message="Failed to load API keys." />}
			{keys.length === 0 && !keysError && (
				<EmptyRow icon={<Key />}>
					No keys. Agents call this toolkit via their own identity or a service
					account&rsquo;s <code className="font-mono">sak_</code> key.
				</EmptyRow>
			)}
			<AnimatePresence initial={false}>
				{keys.map((key) => (
					<KeyRow
						key={key.key_id}
						toolkitKey={key}
						onRevoke={() => revokeKey.mutate(key.key_id)}
						onRename={(label) =>
							updateKey.mutate({ keyId: key.key_id, body: { label } })
						}
						renamePending={updateKey.isPending}
					/>
				))}
			</AnimatePresence>
		</DetailSection>
	);
}
