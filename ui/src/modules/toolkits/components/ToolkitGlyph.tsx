// The toolkit identity tile now lives in `shared/ui` so every module renders
// it identically (e.g. the agent console's bound-toolkit rows). Re-exported
// here to keep the toolkits module's import path stable.
export { ToolkitGlyph, type ToolkitGlyphProps } from '@/shared/ui';
