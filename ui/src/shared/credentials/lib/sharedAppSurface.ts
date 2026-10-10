/**
 * The "register as a shared app" seam of the connect approve dialog.
 *
 * An OAuth API with no app to connect through leaves its connect session
 * `awaiting_app`. One way to resolve it is to register an organisation-wide
 * OAuth app for the API, which only a host with a shared-app admin surface
 * offers. Such a host provides this context around the app; without a
 * provider the option is not shown. The dialog detects the capability by the
 * provider's presence, never by an edition flag.
 */
import { createContext, useContext, type ReactNode } from 'react';

export interface SharedAppRegistrationRequest {
	/** The registry API the agent asked for. */
	api: { vendor: string; name: string | null; version: string | null };
	/** The API's display name, for the host's copy. */
	displayName: string;
	/**
	 * Call once a shared app is registered: the dialog reloads the review,
	 * and the session resolves through the new app.
	 */
	onRegistered: () => void;
}

export interface SharedAppSurface {
	/** Render the host's register-a-shared-app entry (e.g. a button opening its form). */
	renderRegister: (request: SharedAppRegistrationRequest) => ReactNode;
}

export const SharedAppSurfaceContext = createContext<SharedAppSurface | null>(null);

/** The host's shared-app surface, or null when this host has none. */
export function useSharedAppSurface(): SharedAppSurface | null {
	return useContext(SharedAppSurfaceContext);
}
