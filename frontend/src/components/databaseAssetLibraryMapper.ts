import type {
  AssetLibraryItem,
  AssetLibraryItemKind,
  VersionSnapshot,
} from './CoCreationAgentWorkspace.types';
import type { DatabaseAssetRecord } from '../services/cocreationHistoryService';

const extensionOf = (filename: string): string =>
  filename.split('.').pop()?.toLowerCase() || '';

const PROMPT_KINDS = new Set(['prompt']);
const CAD_EXTENSIONS = new Set(['step', 'stp', 'iges', 'igs']);
const MODEL_EXTENSIONS = new Set(['stl', 'obj', 'glb', 'gltf', '3mf']);
const SCRIPT_EXTENSIONS = new Set(['js', 'mjs', 'ts', 'jscad']);
const DOCUMENT_EXTENSIONS = new Set(['pdf', 'doc', 'docx', 'txt', 'md']);
const ARCHIVE_EXTENSIONS = new Set(['zip', 'rar', '7z', 'tar', 'gz']);

function assetKind(record: DatabaseAssetRecord): AssetLibraryItemKind {
  const rawKind = record.kind.trim().toLowerCase();
  const extension = extensionOf(record.filename);
  const hasPrompt = typeof record.metadata.prompt === 'string';
  if (PROMPT_KINDS.has(rawKind) || (rawKind === 'text' && hasPrompt)) {
    return 'prompt';
  }
  if (record.contentType.startsWith('image/') || rawKind === 'image') {
    return 'image';
  }
  if (record.contentType.startsWith('audio/') || rawKind === 'audio') {
    return 'audio';
  }
  if (rawKind === 'cad' || CAD_EXTENSIONS.has(extension)) {
    return 'cad';
  }
  if (rawKind === 'model' || MODEL_EXTENSIONS.has(extension)) {
    return 'model';
  }
  if (rawKind === 'script' || SCRIPT_EXTENSIONS.has(extension)) {
    return 'script';
  }
  if (rawKind === 'document' || DOCUMENT_EXTENSIONS.has(extension)) {
    return 'document';
  }
  if (rawKind === 'archive' || ARCHIVE_EXTENSIONS.has(extension)) {
    return 'archive';
  }
  return 'other';
}

export function mapDatabaseAssets(
  records: DatabaseAssetRecord[],
  snapshots: VersionSnapshot[],
): AssetLibraryItem[] {
  return records.map((record) => {
    const sourceVersion = snapshots.find(
      (snapshot) =>
        snapshot.id === record.sourceVersionId
        && (!record.projectId || snapshot.projectId === record.projectId),
    );
    const kind = assetKind(record);
    const sourceProjectId =
      sourceVersion?.projectId
      || sourceVersion?.sourceProjectId
      || record.projectId
      || '';
    const sourceProjectName =
      sourceVersion?.projectName
      || sourceVersion?.sourceObject
      || sourceProjectId
      || '数据库资产';
    const descriptionValue = record.metadata.description;
    const promptValue = record.metadata.prompt;
    const downloadUrl =
      record.downloadUrl || `/api/v1/assets/${encodeURIComponent(record.id)}/download`;
    return {
      id: record.id,
      kind,
      projectId: sourceProjectId,
      projectName: sourceProjectName,
      versionNumber: sourceVersion?.versionNumber || 0,
      title: record.filename,
      description:
        typeof descriptionValue === 'string'
          ? descriptionValue
          : sourceVersion?.resultText || sourceVersion?.note || '数据库资产',
      prompt:
        kind === 'prompt'
          ? (
            typeof promptValue === 'string'
              ? promptValue
              : sourceVersion?.prompt
          )
          : undefined,
      imageUrl: kind === 'image' ? downloadUrl : null,
      downloadUrl,
      sourceProjectName,
      sourceProjectId,
      sourceVersionId: record.sourceVersionId || sourceVersion?.id || '',
      sourceVersionLabel: sourceVersion?.label || record.sourceVersionId || '数据库资产',
      createdAt: record.createdAt,
      isFinalized: true,
    };
  });
}
