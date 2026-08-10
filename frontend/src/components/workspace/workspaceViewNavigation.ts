import type { WorkspacePrimaryView } from './workspaceResourceTypes';

interface BuildWorkspaceViewPathInput {
  pathname: string;
  search: string;
  next: WorkspacePrimaryView;
}

interface BuildProjectLinkedPathInput {
  pathname: string;
  search: string;
  projectId: string;
  projectName: string;
}

export const buildWorkspaceViewPath = ({
  pathname,
  search,
  next,
}: BuildWorkspaceViewPathInput): string => {
  const params = new URLSearchParams(search);
  if (next === 'chat') {
    params.delete('view');
    params.delete('archiveProject');
  } else {
    params.set('view', next);
    if (next !== 'projects') params.delete('archiveProject');
  }
  const query = params.toString();
  return query ? `${pathname}?${query}` : pathname;
};

export const buildProjectLinkedPath = ({
  pathname,
  search,
  projectId,
  projectName,
}: BuildProjectLinkedPathInput): string => {
  const params = new URLSearchParams(search);
  params.set('project', projectId);
  if (projectName) params.set('name', projectName);
  params.delete('prompt');
  params.delete('archiveProject');
  const query = params.toString();
  return query ? `${pathname}?${query}` : pathname;
};
