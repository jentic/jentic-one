import { motion } from 'framer-motion';
import { KeyRound, Layers, ShieldCheck, Wrench } from 'lucide-react';
import { Button } from '@/components/ui/Button';

/**
 * Polished empty state for the Toolkits list.
 *
 * Adapted from the `jentic-webapp` collections empty state, but reframed
 * for *our* notion of a toolkit (an agent-scoped bundle of API keys +
 * bound credentials) rather than "connect an API". We keep the decorative
 * blurred glow + a central springing icon tile + animated copy + a 3-up
 * feature grid, dropping the brand-logo constellation (toolkits aren't
 * vendors). All motion is `framer-motion`, matching the rest of the app.
 *
 * Copy is preserved verbatim for the strings the tests pin:
 * "No toolkits yet" and "Create your first toolkit".
 */
const FEATURES = [
	{
		icon: KeyRound,
		title: 'Scoped API keys',
		body: 'Issue revocable keys per toolkit',
		tile: 'bg-emerald-500/10 text-emerald-500',
	},
	{
		icon: ShieldCheck,
		title: 'Bound credentials',
		body: 'Grant only the access an agent needs',
		tile: 'bg-blue-500/10 text-blue-500',
	},
	{
		icon: Layers,
		title: 'Reusable bundles',
		body: 'Group integrations for each use case',
		tile: 'bg-violet-500/10 text-violet-500',
	},
] as const;

export interface ToolkitsEmptyStateProps {
	onCreate: () => void;
}

export function ToolkitsEmptyState({ onCreate }: ToolkitsEmptyStateProps) {
	return (
		<div className="border-border/60 from-muted/30 relative overflow-hidden rounded-2xl border border-dashed bg-gradient-to-b to-transparent">
			{/* Decorative blurred blobs */}
			<div
				className="pointer-events-none absolute inset-0 overflow-hidden"
				aria-hidden="true"
			>
				<div className="bg-primary/5 absolute -top-24 -right-24 h-64 w-64 rounded-full blur-3xl" />
				<div className="bg-primary/5 absolute -bottom-24 -left-24 h-64 w-64 rounded-full blur-3xl" />
			</div>

			<div className="relative flex flex-col items-center px-6 py-14 text-center sm:py-16">
				{/* Central icon tile with pulsing glow */}
				<div className="relative mb-6 flex items-center justify-center">
					<div className="bg-primary/20 absolute h-16 w-16 animate-pulse rounded-full blur-xl" />
					<motion.div
						initial={{ opacity: 0, scale: 0 }}
						animate={{ opacity: 1, scale: 1 }}
						transition={{ type: 'spring', stiffness: 260, damping: 20 }}
						className="from-primary/80 to-primary relative flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br shadow-lg"
					>
						<Wrench className="h-7 w-7 text-white" aria-hidden="true" />
					</motion.div>
				</div>

				<motion.h2
					initial={{ opacity: 0, y: 8 }}
					animate={{ opacity: 1, y: 0 }}
					transition={{ delay: 0.15 }}
					className="font-heading text-foreground text-lg font-semibold"
				>
					No toolkits yet
				</motion.h2>
				<motion.p
					initial={{ opacity: 0, y: 8 }}
					animate={{ opacity: 1, y: 0 }}
					transition={{ delay: 0.25 }}
					className="text-muted-foreground mt-1.5 max-w-md text-sm"
				>
					Create a toolkit to give an agent scoped access to your APIs — bundle the
					credentials it needs and hand out revocable keys.
				</motion.p>

				<motion.div
					initial={{ opacity: 0, y: 8 }}
					animate={{ opacity: 1, y: 0 }}
					transition={{ delay: 0.35 }}
					className="mt-6"
				>
					<Button size="lg" onClick={onCreate}>
						Create your first toolkit
					</Button>
				</motion.div>

				{/* Feature grid */}
				<motion.div
					initial={{ opacity: 0, y: 8 }}
					animate={{ opacity: 1, y: 0 }}
					transition={{ delay: 0.45 }}
					className="mt-10 grid w-full max-w-2xl gap-3 sm:grid-cols-3"
				>
					{FEATURES.map((f) => {
						const Icon = f.icon;
						return (
							<div
								key={f.title}
								className="border-border/40 bg-card/50 rounded-xl border p-4 text-left"
							>
								<div
									className={`mb-2.5 flex h-9 w-9 items-center justify-center rounded-lg ${f.tile}`}
								>
									<Icon className="h-5 w-5" aria-hidden="true" />
								</div>
								<p className="text-foreground text-sm font-medium">{f.title}</p>
								<p className="text-muted-foreground mt-0.5 text-xs">{f.body}</p>
							</div>
						);
					})}
				</motion.div>
			</div>
		</div>
	);
}
