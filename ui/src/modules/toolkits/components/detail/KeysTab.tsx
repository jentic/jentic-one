import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { Ban, Key, Plus } from 'lucide-react';
import { Badge, Button, ErrorAlert, Input } from '@/shared/ui';
import { useCreateKey, useRevokeKey, useToolkitKeys } from '@/modules/toolkits/api';
import { InlineConfirm } from '@/modules/toolkits/components/InlineConfirm';
import { OneTimeKeyDisplay } from '@/modules/toolkits/components/OneTimeKeyDisplay';
import {
	DetailSection,
	EmptyRow,
	panelMotion,
	rowMotion,
} from '@/modules/toolkits/components/detail/shared';
import { timeAgo } from '@/modules/toolkits/lib/time';

/**
 * Keys tab — static toolkit API keys. Creation is an inline panel (not a
 * dialog) so the one-time plaintext reveal (`OneTimeKeyDisplay`) lands in the
 * same visual flow; the plaintext is wiped the moment the user confirms
 * (sensitive-data rule — it never persists across a dismissal).
 */
export function KeysTab({ toolkitId, suspended }: { toolkitId: string; suspended: boolean }) {
	const { data: keys = [], isError: keysError } = useToolkitKeys(toolkitId);
	const createKey = useCreateKey(toolkitId);
	const revokeKey = useRevokeKey(toolkitId);

	const [showKeyCreate, setShowKeyCreate] = useState(false);
	const [keyName, setKeyName] = useState('');
	const [newKey, setNewKey] = useState<string | null>(null);

	const submitKey = () => {
		createKey.mutate(
			{ label: keyName || null },
			{
				onSuccess: (res) => {
					setNewKey(res.api_key);
					setShowKeyCreate(false);
					setKeyName('');
				},
			},
		);
	};

	return (
		<DetailSection
			title={`API Keys (${keys.length})`}
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
					<motion.div
						key={key.key_id}
						{...rowMotion}
						layout
						className="bg-muted/30 border-border/60 hover:border-border flex flex-wrap items-center gap-3 overflow-hidden rounded-lg border p-3 transition-colors"
					>
						<div className="bg-accent-yellow/10 text-accent-yellow flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
							<Key className="h-4 w-4" />
						</div>
						<div className="min-w-0 flex-1 basis-40">
							<div className="flex items-center gap-2">
								<span className="text-foreground truncate text-sm font-medium">
									{key.label || 'Unnamed Key'}
								</span>
								<code className="text-muted-foreground font-mono text-xs">
									{key.key_preview}
								</code>
								{key.revoked && <Badge variant="danger">revoked</Badge>}
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
									onConfirm={() => revokeKey.mutate(key.key_id)}
									message="Revoke this key?"
									confirmLabel="Revoke"
								>
									<Button
										variant="danger"
										size="sm"
										className="px-2 py-1 text-xs"
									>
										Revoke
									</Button>
								</InlineConfirm>
							</div>
						)}
					</motion.div>
				))}
			</AnimatePresence>
		</DetailSection>
	);
}
