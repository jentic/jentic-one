import { useState, useCallback } from 'react';
import { KeyRound, LogOut, Settings } from 'lucide-react';
import { AppLink } from '@/shared/ui/AppLink';
import { Button } from '@/shared/ui/Button';
import { MenuPanel, MenuSeparator, menuItemClass, useDismissable } from '@/shared/ui/Menu';
import { cn } from '@/shared/lib/utils';
import { useAuth } from '@/shared/auth/AuthContext';
import { useVersionInfo } from '@/shared/hooks';
import { ROUTES } from '@/shared/app/routes';

/** Avatar initial: first name, then email, then a neutral fallback. */
function avatarInitial(name: string | null | undefined, email: string | null | undefined): string {
	const source = name?.trim() || email?.trim() || 'U';
	return source[0]!.toUpperCase();
}

/** Best-effort display name: "First Last", else first name, else email. */
function displayName(
	first: string | null | undefined,
	last: string | null | undefined,
	email: string | null | undefined,
): string {
	const full = [first, last].filter(Boolean).join(' ').trim();
	return full || email || 'User';
}

export function UserMenu() {
	const { user, logout } = useAuth();
	const { current } = useVersionInfo();
	const [open, setOpen] = useState(false);
	const close = useCallback(() => setOpen(false), []);
	const menuRef = useDismissable<HTMLDivElement>(open, close);

	const initial = avatarInitial(user?.first_name, user?.email);

	return (
		<div ref={menuRef} className="relative">
			<Button
				variant="ghost"
				onClick={() => setOpen((o) => !o)}
				aria-haspopup="menu"
				aria-expanded={open}
				aria-label="User menu"
				className="bg-primary/80 text-background hover:bg-primary/80 hover:text-background relative h-7 w-7 rounded-full p-0 text-xs font-semibold hover:opacity-80"
			>
				{initial}
			</Button>

			{open && (
				<MenuPanel align="right" className="mt-2 w-60">
					<div className="px-2.5 pt-2 pb-2">
						<div className="text-foreground truncate text-sm font-semibold">
							{displayName(user?.first_name, user?.last_name, user?.email)}
						</div>
						{user?.email && (
							<div className="text-muted-foreground/70 mt-0.5 truncate text-xs">
								{user.email}
							</div>
						)}
						{current && (
							<div className="text-muted-foreground/60 mt-1 truncate text-[11px]">
								jentic-one v{current}
							</div>
						)}
					</div>

					<MenuSeparator />

					<AppLink
						href={ROUTES.changePassword}
						role="menuitem"
						onClick={close}
						className={menuItemClass()}
					>
						<KeyRound className="h-4 w-4 shrink-0" aria-hidden="true" />
						Change password
					</AppLink>

					<AppLink
						href="/settings"
						role="menuitem"
						onClick={close}
						className={menuItemClass()}
					>
						<Settings className="h-4 w-4 shrink-0" aria-hidden="true" />
						Settings
					</AppLink>

					<MenuSeparator />

					<Button
						variant="ghost"
						role="menuitem"
						onClick={() => {
							close();
							logout();
						}}
						className={cn(menuItemClass(), 'justify-start')}
					>
						<LogOut className="h-4 w-4 shrink-0" aria-hidden="true" />
						Sign out
					</Button>
				</MenuPanel>
			)}
		</div>
	);
}
