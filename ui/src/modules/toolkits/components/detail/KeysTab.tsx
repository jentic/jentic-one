import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Ban, Check, Globe, Key, Pencil, Plus, X } from 'lucide-react';
import { Badge, Button, ErrorAlert, Input } from '@/shared/ui';
import { useCreateKey, useRevokeKey, useToolkitKeys, useUpdateKey } from '@/modules/toolkits/api';
import { InlineConfirm } from '@/modules/toolkits/components/InlineConfirm';
import { OneTimeKeyDisplay } from '@/modules/toolkits/components/OneTimeKeyDisplay';
import {
	DetailSection,
	EmptyRow,
	panelMotion,
	rowMotion,
} from '@/modules/toolkits/components/detail/shared';
import { timeAgo } from '@/modules/toolkits/lib/time';
import type { ToolkitKey } from '@/modules/toolkits/api/types';

/**
 * Keys tab — static toolkit API keys. Creation is an inline panel (not a
 * dialog) so the one-time plaintext reveal (`OneTimeKeyDisplay`) lands in the
 * same visual flow; the plaintext is wiped the moment the user confirms
 * (sensitive-data rule — it never persists across a dismissal).
 *
 * Keys also carry two long-supported-but-previously-hidden PATCH fields:
 * label rename (inline pencil) and `allowed_ips` (set at create, shown as a
 * chip) — see the phase-4 plan.
 */

/** Parse a comma/space separated IP list into the wire array (null = no restriction). */
function parseIps(raw: string): string[] | null {
	const ips = raw
		.split(/[\s,]+/)
		.map((s) => s.trim())
		.filter(Boolean);
	return ips.length > 0 ? ips : null;
}

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
								{key.label || 'Unnamed Key'}
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
					{key.revoked && <Badge variant="danger">revoked</Badge>}
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
	const createKey = useCreateKey(toolkitId);
	const revokeKey = useRevokeKey(toolkitId);
	const updateKey = useUpdateKey(toolkitId);

	const [showKeyCreate, setShowKeyCreate] = useState(false);
	const [keyName, setKeyName] = useState('');
	const [keyIps, setKeyIps] = useState('');
	const [newKey, setNewKey] = useState<string | null>(null);

	const submitKey = () => {
		createKey.mutate(
			{ label: keyName || null, allowed_ips: parseIps(keyIps) },
			{
				onSuccess: (res) => {
					setNewKey(res.api_key);
					setShowKeyCreate(false);
					setKeyName('');
					setKeyIps('');
				},
			},
		);
	};

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
			action={
				suspended
					? undefined
					: {
							label: (
								<>
									<Plus className="h-4 w-4" /> Create Key
								</>
							),
							onClick: () => setShowKeyCreate(true),
							variant: 'primary',
						}
			}
		>
			<AnimatePresence initial={false}>
				{newKey && (
					<motion.div key="one-time-key" {...panelMotion} className="overflow-hidden">
						<OneTimeKeyDisplay keyValue={newKey} onConfirm={() => setNewKey(null)} />
					</motion.div>
				)}
				{showKeyCreate && (
					<motion.div key="create-key-form" {...panelMotion} className="overflow-hidden">
						<div className="bg-muted/30 border-border/60 space-y-3 rounded-lg border p-4">
							<p className="text-foreground text-sm font-semibold">Create API Key</p>
							<Input
								type="text"
								value={keyName}
								onChange={(e) => setKeyName(e.target.value)}
								placeholder="Key label (optional)"
								aria-label="Key label"
								autoFocus
								onKeyDown={(e) => {
									if (e.key === 'Enter' && !createKey.isPending) submitKey();
								}}
							/>
							<div>
								<Input
									type="text"
									value={keyIps}
									onChange={(e) => setKeyIps(e.target.value)}
									placeholder="Allowed IPs, comma-separated (optional)"
									aria-label="Allowed IPs"
									onKeyDown={(e) => {
										if (e.key === 'Enter' && !createKey.isPending) submitKey();
									}}
								/>
								<p className="text-muted-foreground mt-1 text-xs">
									Restrict where this key may be used from. Leave empty to allow
									any address.
								</p>
							</div>
							<div className="flex gap-2">
								<Button size="sm" onClick={submitKey} loading={createKey.isPending}>
									{createKey.isPending ? 'Generating...' : 'Generate'}
								</Button>
								<Button
									variant="secondary"
									size="sm"
									onClick={() => {
										setShowKeyCreate(false);
										setKeyName('');
										setKeyIps('');
									}}
								>
									Cancel
								</Button>
							</div>
						</div>
					</motion.div>
				)}
			</AnimatePresence>
			{keysError && <ErrorAlert message="Failed to load API keys." />}
			{keys.length === 0 && !showKeyCreate && !newKey && !keysError && (
				<EmptyRow icon={<Key />}>
					No keys yet. Create one to let agents call this toolkit with a static key.
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
