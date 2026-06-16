import { useRef } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { Button } from './Button';
import { Input } from './Input';
import { Select } from './Select';
import type { PermissionRule } from '@/api/types';

interface PermissionRuleEditorProps {
	rules: PermissionRule[];
	onChange: (rules: PermissionRule[]) => void;
}

const emptyRule = (): PermissionRule => ({ effect: 'allow', path: '', methods: [] });

export function PermissionRuleEditor({ rules, onChange }: PermissionRuleEditorProps) {
	// Stable per-row keys so editing a field doesn't remount the input (which
	// would drop focus / caret). Rules carry no id and only support add/remove
	// at the boundaries, so we reconcile a parallel id list by length.
	const idsRef = useRef<number[]>([]);
	const seqRef = useRef(0);
	if (idsRef.current.length < rules.length) {
		while (idsRef.current.length < rules.length) idsRef.current.push(seqRef.current++);
	} else if (idsRef.current.length > rules.length) {
		idsRef.current = idsRef.current.slice(0, rules.length);
	}
	const ids = idsRef.current;

	const addRule = () => onChange([...rules, emptyRule()]);
	const removeRule = (i: number) => {
		idsRef.current = idsRef.current.filter((_, idx) => idx !== i);
		onChange(rules.filter((_, idx) => idx !== i));
	};
	const updateRule = (i: number, patch: Partial<PermissionRule>) => {
		const updated = rules.map((r, idx) => (idx === i ? { ...r, ...patch } : r));
		onChange(updated);
	};

	return (
		<div className="space-y-2">
			{rules.map((rule, i) => (
				<div
					key={ids[i]}
					className="bg-muted/30 border-border/60 flex flex-col gap-2 rounded-lg border p-3 sm:flex-row sm:items-start"
				>
					{/* Effect */}
					<Select
						value={rule.effect}
						onChange={(e) =>
							updateRule(i, { effect: e.target.value as 'allow' | 'deny' })
						}
						className="w-full sm:w-auto"
					>
						<option value="allow">Allow</option>
						<option value="deny">Deny</option>
					</Select>

					{/* Path */}
					<Input
						type="text"
						value={rule.path ?? ''}
						onChange={(e) => updateRule(i, { path: e.target.value || null })}
						placeholder="/path/prefix or *"
						className="w-full flex-1 font-mono"
					/>

					{/* Methods */}
					<div className="flex items-start gap-2">
						<Input
							type="text"
							value={rule.methods?.join(', ') ?? ''}
							onChange={(e) =>
								updateRule(i, {
									methods: e.target.value
										? e.target.value
												.split(',')
												.map((s) => s.trim().toUpperCase())
												.filter(Boolean)
										: null,
								})
							}
							placeholder="GET, POST (blank=any)"
							className="w-full flex-1 font-mono sm:w-40 sm:flex-none"
						/>

						<Button
							variant="ghost"
							size="icon"
							onClick={() => removeRule(i)}
							aria-label="Remove rule"
							className="text-danger hover:text-danger/80 mt-1 shrink-0"
						>
							<Trash2 className="h-4 w-4" />
						</Button>
					</div>
				</div>
			))}

			<Button type="button" variant="secondary" size="sm" onClick={addRule}>
				<Plus className="mr-1 h-4 w-4" /> Add Rule
			</Button>
		</div>
	);
}
