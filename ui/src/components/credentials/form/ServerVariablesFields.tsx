import { Input } from '@/components/ui/Input';
import { Label } from '@/components/ui/Label';
import { Select } from '@/components/ui/Select';
import type { ServerVarDef } from '@/hooks/useApiServerVarDefs';

/**
 * Renders the server-variables block when the selected API uses a
 * templated base URL (OpenAPI 3 `servers[].variables`). Examples
 * we've seen in the wild:
 *
 *  - Discourse: `defaultHost` (the user's instance domain).
 *  - Salesforce: `instance` (user's org subdomain).
 *  - Atlassian: `siteName` (cloud site).
 *
 * Each variable renders as either:
 *  - a `<Select>` if the spec declares an `enum` of allowed values
 *    (rare but happens — e.g. region selectors), OR
 *  - an `<Input>` otherwise.
 *
 * Required-marker logic: `varDef.required` is the source of truth.
 * For local APIs the backend sets it explicitly; for catalog APIs
 * `useApiServerVarDefs` derives it from "no default = required".
 *
 * Pure presentation. State for `values` is owned by the parent
 * `CredentialFormFields` so the submit handler can validate +
 * serialise in one place. The parent passes the merged map; we
 * pass back individual key updates.
 */
export interface ServerVariablesFieldsProps {
	defs: ServerVarDef[];
	values: Record<string, string>;
	onChange: (name: string, value: string) => void;
}

export function ServerVariablesFields({ defs, values, onChange }: ServerVariablesFieldsProps) {
	if (defs.length === 0) return null;
	return (
		<div className="bg-muted/30 border-border space-y-3 rounded-lg border p-4">
			<div>
				<p className="text-foreground text-sm font-medium">Server configuration</p>
				<p className="text-muted-foreground mt-0.5 text-xs">
					This API uses a templated base URL. Fill in the values for your instance.
				</p>
			</div>
			{defs.map((varDef) => (
				<div key={varDef.name}>
					<Label
						htmlFor={`svar-${varDef.name}`}
						className="text-muted-foreground mb-1 block text-xs"
					>
						<span className="font-mono">{varDef.name}</span>
						{varDef.required && <span className="text-danger ml-1">*</span>}
					</Label>
					{varDef.description && (
						<p className="text-muted-foreground mb-1 text-xs">{varDef.description}</p>
					)}
					{varDef.enum && varDef.enum.length > 0 ? (
						<Select
							id={`svar-${varDef.name}`}
							value={values[varDef.name] ?? varDef.default ?? ''}
							onChange={(e) => onChange(varDef.name, e.target.value)}
							className="bg-background border-border text-foreground w-full rounded-md border px-3 py-2 text-sm"
						>
							{varDef.enum.map((opt) => (
								<option key={opt} value={opt}>
									{opt}
								</option>
							))}
						</Select>
					) : (
						<Input
							id={`svar-${varDef.name}`}
							type="text"
							value={values[varDef.name] ?? ''}
							onChange={(e) => onChange(varDef.name, e.target.value)}
							placeholder={varDef.default ?? `Enter ${varDef.name}`}
							className="bg-background font-mono"
						/>
					)}
				</div>
			))}
		</div>
	);
}
