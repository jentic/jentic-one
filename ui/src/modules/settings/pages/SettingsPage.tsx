/**
 * SettingsPage — the settings surface shell: a section nav + the active
 * section's content.
 *
 * Layout: the shell is a flex COLUMN with a min-height filling the Layout's
 * content area (100dvh minus the fixed h-12 TopNavbar and `<main>`'s bottom
 * padding — `pb-20` under the mobile BottomNavbar, `md:pb-12` on desktop), so
 * the section row can `flex-1`-stretch and the sidebar's `border-r` runs to
 * the bottom of the viewport even when the content is short. It's a
 * MIN-height, not a height: long content (the OAuth clients roster) grows
 * past it and the DOCUMENT scrolls — no nested scroll container.
 *
 * Responsive: the persistent side column is a desktop (`md+`) grammar. On
 * phones the section nav collapses to the platform's horizontal `TabNav`
 * above the content — a hard `w-56` column would eat half of a 375px screen.
 */
import { useState } from 'react';
import { Code2 } from 'lucide-react';
import { Button, PageHeader, PageShell, TabNav, type TabNavOption } from '@/shared/ui';
import { cn } from '@/shared/lib/utils';
import { OAuthClientsSection } from './OAuthClientsSection';

type SettingsSection = 'developer';

const SECTIONS: { id: SettingsSection; label: string }[] = [
	{ id: 'developer', label: 'Developer Settings' },
];

const SECTION_ICONS: Record<SettingsSection, React.ReactNode> = {
	developer: <Code2 className="h-4 w-4" aria-hidden="true" />,
};

export function SettingsPage() {
	const [activeSection, setActiveSection] = useState<SettingsSection>('developer');

	const tabOptions: TabNavOption<SettingsSection>[] = SECTIONS.map((s) => ({
		value: s.id,
		label: s.label,
		icon: SECTION_ICONS[s.id],
	}));

	return (
		<PageShell
			spacing="space-y-0"
			// pb-0: the shell's own bottom padding would stop the sidebar
			// border short of the bottom edge; the content column carries its
			// own py instead.
			className="flex min-h-[calc(100dvh-8rem)] flex-col pb-0 md:min-h-[calc(100dvh-6rem)]"
		>
			<PageHeader
				title="Settings"
				subtitle="Manage your organization's configuration and integrations."
			/>

			{/* Phone grammar: horizontal section tabs above the content. */}
			<TabNav<SettingsSection>
				options={tabOptions}
				value={activeSection}
				onChange={setActiveSection}
				ariaLabel="Settings sections"
				className="mt-2 md:hidden"
			/>

			<div className="flex min-h-0 flex-1 flex-col md:flex-row">
				{/* Desktop grammar: persistent side column. */}
				<nav
					aria-label="Settings sections"
					className="border-border hidden w-56 shrink-0 border-r py-6 pr-4 md:block"
				>
					<div className="space-y-1">
						{SECTIONS.map((section) => (
							<Button
								key={section.id}
								variant="ghost"
								size="sm"
								onClick={(): void => setActiveSection(section.id)}
								aria-current={activeSection === section.id ? 'page' : undefined}
								className={cn(
									'w-full justify-start gap-2',
									activeSection === section.id && 'bg-accent text-foreground',
								)}
							>
								{SECTION_ICONS[section.id]}
								{section.label}
							</Button>
						))}
					</div>
				</nav>

				{/* Content flows with the document (no nested scroll container —
				    the old overflow-y-auto sat in an unbounded column and never
				    actually scrolled; the roster scrolls with the page). */}
				<div className="min-w-0 flex-1 py-6 md:pl-6">
					{activeSection === 'developer' && <OAuthClientsSection />}
				</div>
			</div>
		</PageShell>
	);
}
