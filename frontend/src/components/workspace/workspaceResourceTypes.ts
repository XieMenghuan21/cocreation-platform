export type WorkspacePrimaryView =
  | 'chat'
  | 'projects'
  | 'files'
  | 'assets'
  | 'versions'
  | 'quotes';

const WORKSPACE_VIEWS = new Set<WorkspacePrimaryView>([
  'chat',
  'projects',
  'files',
  'assets',
  'versions',
  'quotes',
]);

export const normalizeWorkspacePrimaryView = (value: string | null | undefined): WorkspacePrimaryView => {
  if (!value || !WORKSPACE_VIEWS.has(value as WorkspacePrimaryView)) return 'chat';
  return value as WorkspacePrimaryView;
};
