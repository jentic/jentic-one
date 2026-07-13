// The actor status pill now lives in `shared/ui` so every module renders it
// identically. Re-exported here to keep the agents module's import path stable.
export { ActorStatusBadge } from '@/shared/ui';
