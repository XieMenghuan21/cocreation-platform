import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Send,
  Loader2,
  Sparkles,
  MessageSquare,
  Image as ImageIcon,
  CheckCircle2,
  XCircle,
  X,
  Boxes,
  PanelRightClose,
  PanelRight,
  PanelLeft,
  FileText,
  Paperclip,
  Mic,
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react';
import { ResizablePanel } from './ResizablePanel';
import PreviewImage from './PreviewImage';
import { GeneratedStlPreview } from './ThreeMeshPreview';
import {
  createIndustrialDesignWorkflow,
  getIndustrialDesignWorkflowTask,
  createEngineeringPackage,
  type CadAiTaskStatus,
  type IndustrialDesignWorkflowPayload,
} from '../services/forgecadService';
import { agentService, type IntentAnalysis } from '../services/agentService';
import { normalizePreviewImageSource } from '../utils/previewImage';
import { getCadAiOutputValue } from './CoCreationAgentWorkspace.helpers';
import type { WorkflowCard } from './workflowCards/types';
import { WorkflowCardView } from './workflowCards';
import { cocreationHistoryService } from '../services/cocreationHistoryService';
import { getVersionsForProject, normalizeVersionSnapshots } from './CoCreationAgentWorkspace.helpers';
import type { VersionSnapshot } from './CoCreationAgentWorkspace.types';
import { conversationService } from '../services/conversationService';
import { assetService, assetDownloadUrl } from '../services/assetService';
import { aggregationWorkbenchService } from '../services/aggregationWorkbenchService';
import { workspaceMirrorService } from '../services/workspaceMirrorService';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  status: 'idle' | 'running' | 'completed' | 'failed';
  taskId?: string | null;
  outputs?: CadAiTaskStatus['outputs'] | null;
  projectId?: string | null;
  versionId?: string | null;
  error?: string | null;
  cards?: WorkflowCard[];
}

type ExecLevel = 'fast' | 'standard' | 'deep';

type WorkflowActionKind =
  | 'design_sheet'
  | 'plan_2d'
  | 'render'
  | 'scene_fusion'
  | 'explosion'
  | '3d'
  | 'cad'
  | 'quote'
  | 'package';

const IMAGE_EDIT_ACTIONS = new Set<WorkflowActionKind>(['plan_2d', 'render', 'scene_fusion', 'explosion', '3d']);

const ACTION_LABELS: Record<WorkflowActionKind, string> = {
  design_sheet: '设计图',
  plan_2d: '2D平面图',
  render: '宣传图',
  scene_fusion: '场景融合图',
  explosion: '爆炸图',
  '3d': '立体效果图',
  cad: 'CAD图纸',
  quote: '报价',
  package: '工程包',
};

const NEXT_STEP_RECOMMENDATIONS = [
  { label: '设计图', agent: 'design_agent', icon: 'PencilLine', action: 'design_sheet' as const },
  { label: '2D平面图', agent: 'plan_agent', icon: 'FileText', action: 'plan_2d' as const },
  { label: '宣传图', agent: 'render', icon: 'ImageIcon', action: 'render' as const },
  { label: '场景融合图', agent: 'scene_fusion', icon: 'Layers', action: 'scene_fusion' as const },
  { label: '爆炸图', agent: 'explosion', icon: 'Boxes', action: 'explosion' as const },
  { label: '立体效果图', agent: '3d', icon: 'Box', action: '3d' as const },
  { label: 'CAD 图纸', agent: 'cad', icon: 'FileText', action: 'cad' as const },
  { label: '报价', agent: 'quote', icon: 'Calculator', action: 'quote' as const },
  { label: '工程包', agent: 'package', icon: 'Package', action: 'package' as const },
];

const EXEC_LEVELS: Array<{ id: ExecLevel; label: string }> = [
  { id: 'fast', label: '快速' },
  { id: 'standard', label: '标准' },
  { id: 'deep', label: '深度' },
];

interface GptWorkspaceProps {
  initialPrompt?: string | null;
  projectId?: string | null;
  projectName?: string | null;
  initialPreview?: string | null;
  initialConversationId?: string | null;
  onProjectLinked?: (projectId: string, projectName: string) => void;
  onNavigateHome?: () => void;
  externalResourceCenter?: boolean;
}

const EMPTY_OUTPUTS: CadAiTaskStatus['outputs'] = {};

const nextId = (() => {
  let counter = 0;
  return () => `chat-${Date.now()}-${(counter += 1)}`;
})();

const MessageCardStack: React.FC<{
  cards: WorkflowCard[];
  onAction: (card: WorkflowCard, action: string, extra?: Record<string, unknown>) => void;
}> = ({ cards, onAction }) => {
  const [index, setIndex] = useState(0);
  const safeIndex = Math.min(index, Math.max(cards.length - 1, 0));
  const activeCard = cards[safeIndex];

  useEffect(() => {
    if (index > cards.length - 1) setIndex(Math.max(cards.length - 1, 0));
  }, [cards.length, index]);

  if (!activeCard) return null;

  return (
    <div className="mt-3 overflow-hidden rounded-2xl border border-slate-200 bg-white">
      {cards.length > 1 ? (
        <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
          <button
            type="button"
            disabled={safeIndex === 0}
            onClick={() => setIndex((prev) => Math.max(prev - 1, 0))}
            className="flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium text-slate-500 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-300"
          >
            <ChevronLeft className="size-3" />
            上一步
          </button>
          <span className="text-[11px] font-medium text-slate-400">
            {safeIndex + 1} / {cards.length}
          </span>
          <button
            type="button"
            disabled={safeIndex >= cards.length - 1}
            onClick={() => setIndex((prev) => Math.min(prev + 1, cards.length - 1))}
            className="flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium text-slate-500 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:text-slate-300"
          >
            下一步
            <ChevronRight className="size-3" />
          </button>
        </div>
      ) : null}
      <div className="[&>div]:rounded-none [&>div]:border-0 [&>div]:shadow-none">
        <WorkflowCardView
          card={activeCard}
          onAction={(action, extra) => onAction(activeCard, action, extra)}
        />
      </div>
    </div>
  );
};

export const GptWorkspace: React.FC<GptWorkspaceProps> = ({
  initialPrompt,
  projectId,
  projectName,
  initialPreview,
  initialConversationId,
  onProjectLinked,
  onNavigateHome,
  externalResourceCenter,
}) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const [chatWidth, setChatWidth] = useState(0);
  const [showPreview, setShowPreview] = useState(false);
  const [previewSource, setPreviewSource] = useState<string | null>(null);
  const [previewKind, setPreviewKind] = useState<'image' | 'stl' | 'plan'>('image');
  const [execLevel, setExecLevel] = useState<ExecLevel>('standard');
  const [showResource, setShowResource] = useState(false);
  const [resourceTab, setResourceTab] = useState<'assets' | 'versions' | 'files'>('assets');
  const [resourceData, setResourceData] = useState<{
    assets: Array<{ id: string; url: string; name: string; kind: string; createdAt: string }>;
    versions: Array<{ id: string; label: string; imageUrl: string | null; status: string; createdAt: string }>;
  }>({ assets: [], versions: [] });
  const [pendingAttachments, setPendingAttachments] = useState<Array<{
    assetId: string;
    url: string;
    name: string;
    isImage: boolean;
  }>>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const initialPromptRef = useRef(initialPrompt);
  const didAutoRun = useRef(false);
  const pendingWorkflowRef = useRef<{
    messageId: string;
    text: string;
    intent: IntentAnalysis | null;
    projectId: string;
  } | null>(null);
  const confirmedRequirementRef = useRef<Set<string>>(new Set());
  const [confirmedMessages, setConfirmedMessages] = useState<string[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  const workflowRunningRef = useRef(false);
  const collectedMaterialsRef = useRef<Record<string, string>>({});
  const [materialQuestions, setMaterialQuestions] = useState<string[]>([]);
  const [collectedMaterials, setCollectedMaterials] = useState<Record<string, string>>({});
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const projectCtxRef = useRef<{
    messageId: string;
    projectId: string;
    projectName: string;
    intent: IntentAnalysis | null;
    fullRequirement: string;
  } | null>(null);
  const projectConfirmedRef = useRef(false);
  const runWorkflowRef = useRef<((ctx: {
    messageId: string;
    text: string;
    intent: IntentAnalysis | null;
    projectId: string;
  }) => Promise<void>) | null>(null);

  const minChatWidth = useMemo(() => {
    if (typeof window === 'undefined') return 420;
    return Math.round(window.innerWidth / 3);
  }, []);

  useEffect(() => {
    const containerWidth = bodyRef.current?.clientWidth ?? window.innerWidth;
    setChatWidth(Math.round(containerWidth * 0.6));
  }, []);

  useEffect(() => {
    if (!initialPreview) return;
    const image = normalizePreviewImageSource(initialPreview);
    if (image) {
      setPreviewSource(image);
      setPreviewKind('image');
      setPreviewTab('image');
      setShowPreview(true);
    }
  }, [initialPreview]);

  const buildMessagesFromSnapshot = useCallback((snapshot: VersionSnapshot): ChatMessage[] => {
    const messages: ChatMessage[] = [];
    const prompt = snapshot.prompt?.trim();
    if (prompt) {
      messages.push({
        id: `${snapshot.id}-user`,
        role: 'user',
        text: prompt,
        status: 'completed',
      });
    }
    const images = [
      snapshot.previewImageUrl,
      ...(snapshot.generatedImageUrls ?? []),
    ].filter((url): url is string => Boolean(url && url.trim()));
    const outputs: CadAiTaskStatus['outputs'] = {};
    if (images.length > 0) {
      outputs.renderPng = images[0];
      outputs.enhancedImage = images[0];
      outputs.generatedImageUrls = images;
    }
    const assistant: ChatMessage = {
      id: snapshot.id,
      role: 'assistant',
      text: snapshot.resultText?.trim() || snapshot.executionSummary?.trim() || (images.length > 0 ? '方案已生成，点击下方卡片查看预览。' : '方案已生成。'),
      status: snapshot.status === 'failed' ? 'failed' : 'completed',
      taskId: snapshot.taskId || null,
      projectId: snapshot.projectId || null,
      versionId: snapshot.id,
      outputs,
    };
    if (images.length > 0) {
      assistant.cards = [
        {
          id: `${snapshot.id}-scheme`,
          type: 'design_scheme',
          data: {
            schemeId: snapshot.id,
            name: snapshot.projectName ? `${snapshot.projectName} · ${snapshot.label}` : snapshot.label,
            thumbnails: images,
            materials: [],
            estimatedPrice: null,
            renderUrl: images[0],
            drawingUrl: null,
            outputs,
          },
        },
      ];
    }
    messages.push(assistant);
    return messages;
  }, []);

  const loadProjectHistory = useCallback(async (pid: string) => {
    try {
      const response = await cocreationHistoryService.listAllHistory();
      const snapshots = getVersionsForProject(response.data.snapshots || [], pid);
      if (snapshots.length === 0) return;
      const built: ChatMessage[] = [];
      for (const snapshot of snapshots) {
        built.push(...buildMessagesFromSnapshot(snapshot));
      }
      setMessages((prev) => (prev.length > 0 ? prev : built));
    } catch {
      setMessages((prev) => prev);
    }
  }, [buildMessagesFromSnapshot]);

  useEffect(() => {
    if (!projectId) return;
    void loadProjectHistory(projectId);
  }, [projectId, loadProjectHistory]);

  const loadConversation = useCallback(async (cid: string) => {
    try {
      const conversation = await conversationService.get(cid);
      conversationIdRef.current = cid;
      setConversationId(cid);
      pendingWorkflowRef.current = null;
      collectedMaterialsRef.current = {};
      setPendingAttachments([]);
      setCollectedMaterials({});
      setShowPreview(false);

      const built: ChatMessage[] = conversation.messages.map((message) => {
        const cardData = message.cardData ?? {};
        const outputs = (cardData.outputs as CadAiTaskStatus['outputs'] | undefined) ?? undefined;
        const cards = (cardData.cards as WorkflowCard[] | undefined) ?? undefined;
        return {
          id: `msg-${message.id}`,
          role: message.role,
          text: message.text,
          status: cardData.status === 'failed' ? 'failed' : 'completed',
          taskId: (cardData.taskId as string | undefined) ?? null,
          projectId: (cardData.projectId as string | undefined) ?? null,
          versionId: (cardData.versionId as string | undefined) ?? null,
          error: (cardData.error as string | undefined) ?? null,
          outputs,
          cards,
        };
      });
      if (built.length > 0 && !sendingRef.current) {
        setMessages(built);
      }

      const projectCard = built
        .flatMap((message) => message.cards ?? [])
        .find((card) => card.type === 'project_created');
      const projectCardData = projectCard?.data as { projectId?: string; name?: string } | undefined;
      const latestProjectMessage = [...built].reverse().find((message) => Boolean(message.projectId));
      const firstUser = built.find((message) => message.role === 'user');
      const lastUser = [...built].reverse().find((message) => message.role === 'user');
      const restoredProjectId = conversation.projectId
        || projectCardData?.projectId
        || latestProjectMessage?.projectId
        || projectId
        || null;

      if (restoredProjectId) {
        const restoredProjectName = projectCardData?.name || projectName || conversation.title || restoredProjectId;
        const anchorMessage = built.find((message) =>
          (message.cards ?? []).some((card) => card.type === 'project_created'),
        ) || latestProjectMessage;
        projectCtxRef.current = {
          messageId: anchorMessage?.id || nextId(),
          projectId: restoredProjectId,
          projectName: restoredProjectName,
          intent: null,
          fullRequirement: firstUser?.text || lastUser?.text || '',
        };
        projectConfirmedRef.current = true;
        if (!conversation.projectId) {
          void conversationService.update(cid, {
            projectId: restoredProjectId,
            title: restoredProjectName,
          }).catch(() => undefined);
        }
        onProjectLinked?.(restoredProjectId, restoredProjectName);
      } else {
        projectCtxRef.current = null;
        projectConfirmedRef.current = false;
      }
    } catch {
      // 会话加载失败不阻塞
    }
  }, [onProjectLinked, projectId, projectName]);

  useEffect(() => {
    if (!initialConversationId) return;
    void loadConversation(initialConversationId);
  }, [initialConversationId, loadConversation]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, sending]);

  const applyMessageToPreview = useCallback((message: ChatMessage) => {
    const outputs = message.outputs || EMPTY_OUTPUTS;

    const renderUrl = normalizePreviewImageSource(
      getCadAiOutputValue(outputs, ['renderPng', 'enhancedImage']),
    );
    if (renderUrl) {
      setPreviewSource(renderUrl);
      setPreviewKind('image');
      return true;
    }
    const drawingSvg = getCadAiOutputValue(outputs, ['planLine', 'planLineSvg', 'drawingSvg', 'drawingDxf']);
    if (drawingSvg) {
      setPreviewSource(normalizePreviewImageSource(drawingSvg));
      setPreviewKind('image');
      return true;
    }
    return false;
  }, []);

  const patchMessage = useCallback((id: string, patch: Partial<ChatMessage>) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)));
  }, []);

  const appendReferenceRequiredMessage = useCallback((actionKind: WorkflowActionKind) => {
    const label = ACTION_LABELS[actionKind];
    const msg: ChatMessage = {
      id: nextId(),
      role: 'assistant',
      text: `${label}必须基于已有设计图或参考图做图片编辑。你可以先上传参考图；如果现在没有，就先生成「设计图」。`,
      status: 'completed',
      cards: [
        {
          id: `reference-required-${Date.now()}`,
          type: 'materials_request',
          data: {
            projectName: projectCtxRef.current?.projectName || '当前项目',
            fields: [
              {
                key: 'referenceImage',
                label: '参考图',
                hint: '上传设计图、商品图、草图或竞品参考图后，再生成2D平面图/宣传图/场景融合/爆炸图/立体效果图。',
                collected: Boolean(collectedMaterialsRef.current.referenceAssetId),
              },
            ],
            collected: collectedMaterialsRef.current.referenceImage
              ? { referenceImage: collectedMaterialsRef.current.referenceImage }
              : {},
            required: true,
            description: `图上图任务必须先上传参考图。没有参考图时，请先生成「设计图」，再继续做${label}。`,
          },
        },
        {
          id: `reference-required-next-${Date.now()}`,
          type: 'next_step',
          data: {
            current: 'reference_required',
            recommendations: [
              { label: '先生成设计图', agent: 'design_agent', icon: 'PencilLine', action: 'design_sheet' },
            ],
          },
        },
      ],
    };
    setMessages((prev) => [...prev, msg]);
    void persistMessage('assistant', msg);
  }, []);

  const buildWorkflowOptions = useCallback((intent: IntentAnalysis | null) => {
    const base = intent?.suggestedOptions;
    let options: IndustrialDesignWorkflowPayload['options'];
    if (base) {
      options = {
        generateDrawing: base.generateDrawing,
        generateRender: base.generateRender,
        generateExplosion: base.generateExplosion,
        enhanceImage: base.enhanceImage,
        generateTrellisAsset: false,
        generateCad: base.generateCad,
        generatePlanLine: base.generatePlanLine,
        generateRenderViews: false,
        generateThreePreview: base.generateThreePreview,
        optimizePrompt: true,
        imageModel: null,
        imageProvider: null,
        cadProvider: null,
      };
    } else {
      options = {
        generateDrawing: true,
        generateRender: true,
        generateExplosion: false,
        enhanceImage: false,
        generateTrellisAsset: false,
        generateCad: false,
        generatePlanLine: false,
        generateRenderViews: false,
        generateThreePreview: false,
        optimizePrompt: true,
        imageModel: null,
        imageProvider: null,
        cadProvider: null,
      };
    }
    if (execLevel === 'fast') {
      options.generateDrawing = true;
      options.generateRender = true;
      options.generateExplosion = false;
      options.generateCad = false;
      options.generateThreePreview = false;
      options.enhanceImage = false;
    }
    if (execLevel === 'deep') {
      options.generateDrawing = true;
      options.generateRender = true;
      options.generateExplosion = true;
      options.generateCad = false;
      options.generateThreePreview = false;
    }
    return options;
  }, [execLevel]);

  const persistMessage = useCallback(async (role: 'user' | 'assistant', msg: ChatMessage) => {
    const cid = conversationIdRef.current;
    if (!cid) return;
    try {
      await conversationService.append(cid, {
        role,
        text: msg.text,
        cardData: {
          status: msg.status,
          taskId: msg.taskId || undefined,
          projectId: msg.projectId || undefined,
          versionId: msg.versionId || undefined,
          cards: msg.cards || undefined,
          outputs: msg.outputs || undefined,
          error: msg.error || undefined,
        },
      });
    } catch {
      // 持久化失败不阻塞主流程
    }
  }, []);

  const ensureConversation = useCallback(async () => {
    if (conversationIdRef.current) return conversationIdRef.current;
    try {
      const conversation = await conversationService.create();
      conversationIdRef.current = conversation.id;
      setConversationId(conversation.id);
      return conversation.id;
    } catch {
      return null;
    }
  }, []);

  const updateMaterialsCard = useCallback((nextCollected: Record<string, string>) => {
    setMessages((prev) =>
      prev.map((m) => {
        if (!(m.cards ?? []).some((c) => c.type === 'materials_request')) return m;
        const materialFields = [
          { key: 'referenceImage', label: '参考图', hint: nextCollected.referenceImage ? '已上传参考图' : '可上传类似款式或风格参考图' },
          { key: 'material', label: '材质', hint: nextCollected.material || '金属 / 木材 / 塑料 / 陶瓷 / 玻璃等' },
          { key: 'dimension', label: '尺寸', hint: nextCollected.dimension || '长宽高 / 重量 / 体积' },
          { key: 'budget', label: '预算', hint: nextCollected.budget || '预期造价范围' },
          { key: 'scene', label: '使用场景', hint: nextCollected.scene || '家居 / 办公 / 户外 / 工业等' },
          { key: 'style', label: '风格', hint: nextCollected.style || '简约 / 科技感 / 复古 / 高端等' },
          { key: 'feature', label: '特殊功能', hint: nextCollected.other || '必需的功能或特性' },
          { key: 'brand', label: '品牌规范', hint: '需要遵循的品牌调性或规范' },
        ];
        const materialsCard: WorkflowCard = {
          id: `${m.id}-materials`,
          type: 'materials_request',
          data: {
            projectName: projectCtxRef.current?.projectName || '项目',
            fields: materialFields.map((f) => ({
              ...f,
              collected: Boolean(nextCollected[f.key] || (f.key === 'feature' && nextCollected.other)),
            })),
            collected: nextCollected,
          },
        };
        return {
          ...m,
          cards: (m.cards ?? []).map((c) => (c.type === 'materials_request' ? materialsCard : c)),
        };
      }),
    );
  }, []);

  const promoteOutputsToReference = useCallback((outputs: CadAiTaskStatus['outputs'] | null | undefined) => {
    if (!outputs) return;
    const referenceImage = getCadAiOutputValue(outputs, ['renderPng', 'enhancedImage', 'explosionPng']);
    const referenceAssetId = getCadAiOutputValue(outputs, ['renderPngAssetId', 'enhancedImageAssetId', 'explosionPngAssetId']);
    if (!referenceImage || !referenceAssetId) return;
    const nextCollected = {
      ...collectedMaterialsRef.current,
      referenceImage,
      referenceAssetId,
    };
    collectedMaterialsRef.current = nextCollected;
    setCollectedMaterials(nextCollected);
    updateMaterialsCard(nextCollected);
  }, [updateMaterialsCard]);

  const handleFileUpload = async (file: File) => {
    if (uploading) return;
    setUploading(true);
    try {
      const projectIdValue = projectCtxRef.current?.projectId ?? null;
      const asset = await assetService.upload(file, {
        kind: file.type.startsWith('image/') ? 'image' : 'document',
        source: 'workspace-attachment',
        projectId: projectIdValue || undefined,
      });
      const url = assetDownloadUrl(asset.id);
      setPendingAttachments((prev) => [
        ...prev,
        {
          assetId: asset.id,
          url,
          name: file.name,
          isImage: file.type.startsWith('image/'),
        },
      ]);
      if (file.type.startsWith('image/')) {
        const nextCollected = { ...collectedMaterialsRef.current };
        nextCollected.referenceImage = url;
        nextCollected.referenceAssetId = asset.id;
        collectedMaterialsRef.current = nextCollected;
        setCollectedMaterials(nextCollected);
        updateMaterialsCard(nextCollected);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : '上传失败，请稍后重试';
      const errMsg: ChatMessage = {
        id: nextId(),
        role: 'assistant',
        text: `附件 ${file.name} 上传失败：${message}`,
        status: 'failed',
        error: message,
      };
      setMessages((prev) => [...prev, errMsg]);
      void persistMessage('assistant', errMsg);
    } finally {
      setUploading(false);
    }
  };

  const handleSend = async (rawText?: string) => {
    const text = (rawText ?? input).trim();
    const attachments = pendingAttachments;
    if ((!text && attachments.length === 0) || sending) return;

    const effectiveText = text || '基于参考图生成';

    const attachmentNote = attachments.length > 0
      ? `\n（已附带 ${attachments.length} 个附件：${attachments.map((a) => a.name).join('、')}）`
      : '';

    const userMessage: ChatMessage = {
      id: nextId(),
      role: 'user',
      text: `${effectiveText}${attachmentNote}`,
      status: 'completed',
    };

    if (attachments.length > 0) {
      userMessage.cards = attachments.filter((a) => a.isImage).map((a) => ({
        id: `attach-${a.assetId}`,
        type: 'design_scheme',
        data: {
          schemeId: `attach-${a.assetId}`,
          name: `参考图 · ${a.name}`,
          thumbnails: [a.url],
          materials: [],
          estimatedPrice: null,
          renderUrl: a.url,
          drawingUrl: null,
          outputs: { renderPng: a.url },
        },
      }));
      const nextCollected = { ...collectedMaterialsRef.current };
      const firstImage = attachments.find((a) => a.isImage);
      if (firstImage) {
        nextCollected.referenceImage = firstImage.url;
        nextCollected.referenceAssetId = firstImage.assetId;
      }
      collectedMaterialsRef.current = nextCollected;
      setCollectedMaterials(nextCollected);
    }
    setMessages((prev) => [...prev, userMessage]);
    setPendingAttachments([]);
    setInput('');
    setSending(true);
    sendingRef.current = true;

    const ctx = projectCtxRef.current;

    try {
      const cid = await ensureConversation();
      if (cid) {
        void persistMessage('user', userMessage);
      }

      if (!ctx) {
        await startProjectCreation(effectiveText, userMessage.id);
      } else if (ctx.messageId === userMessage.id) {
        // 相同消息不重复处理
      } else if (!workflowRunningRef.current) {
        await collectMaterial(effectiveText, userMessage.id, ctx);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : '处理失败，请稍后重试';
      const errMsg: ChatMessage = {
        id: nextId(),
        role: 'assistant',
        text: message,
        status: 'failed',
        error: message,
      };
      setMessages((prev) => [...prev, errMsg]);
      void persistMessage('assistant', errMsg);
    } finally {
      setSending(false);
      sendingRef.current = false;
    }
  };

  const startProjectCreation = async (text: string, userMessageId: string) => {
    const assistantMessage: ChatMessage = {
      id: nextId(),
      role: 'assistant',
      text: '正在分析你的需求并创建项目…',
      status: 'running',
    };
    setMessages((prev) => [...prev, assistantMessage]);

    let intent: IntentAnalysis | null = null;
    try {
      const hasReference = pendingAttachments.some((a) => a.isImage)
        || Boolean(collectedMaterialsRef.current.referenceAssetId);
      const intentText = hasReference
        ? `${text}\n（用户已上传参考图，请基于该参考图生成，不需要再询问材料）`
        : text;
      intent = await agentService.analyzeIntent(intentText);
      if (hasReference) {
        intent.needsMaterials = false;
        if (intent.intent === 'design') {
          intent.intent = 'propaganda';
          intent.suggestedOptions.generateRender = true;
          intent.suggestedOptions.generateExplosion = false;
          intent.suggestedOptions.generateDrawing = false;
        }
      }
    } catch {
      intent = null;
    }

    const project = await agentService.createProject({
      name: intent?.projectName || '未命名项目',
      description: intent?.requirementText || text,
      industry: intent?.industry || '装备制造',
      inputMode: 'prompt',
    });

    const cardsRef: WorkflowCard[] = [
      {
        id: `${assistantMessage.id}-project`,
        type: 'project_created',
        data: {
          name: project.name,
          description: project.description || intent?.requirementText || text,
          projectType: intent?.industry || '工业设计',
          projectId: project.id,
        },
      },
    ];

    const needsMaterials = intent?.needsMaterials ?? true;
    setMaterialQuestions([]);
    setCollectedMaterials({});

    const confirmed: ChatMessage = {
      id: assistantMessage.id,
      role: 'assistant',
      text: `项目「${project.name}」已创建，请确认项目信息。`,
      status: 'completed',
      projectId: project.id,
      cards: cardsRef,
    };
    setMessages((prev) => prev.map((m) => (m.id === assistantMessage.id ? confirmed : m)));

    projectCtxRef.current = {
      messageId: assistantMessage.id,
      projectId: project.id,
      projectName: project.name,
      intent,
      fullRequirement: text,
    };
    pendingWorkflowRef.current = {
      messageId: assistantMessage.id,
      text,
      intent,
      projectId: project.id,
    };
    projectConfirmedRef.current = false;

    void ensureConversation().then((cid) => {
      if (!cid) return;
      void conversationService.update(cid, {
        projectId: project.id,
        title: project.name,
      }).catch(() => undefined);
      onProjectLinked?.(project.id, project.name);
      void workspaceMirrorService.safeMirror(cid, {
        sourceKey: `legacy-project:${project.id}`,
        type: 'project',
        status: 'completed',
        title: project.name,
        summary: project.description || intent?.requirementText || text,
        projectId: project.id,
        inputData: { requirement: text, intent },
        uiData: { projection: 'project_card' },
      });
      void persistMessage('assistant', confirmed);
    });
  };

  const showMaterialsStep = useCallback(() => {
    const ctx = projectCtxRef.current;
    if (!ctx) return;
    const intent = ctx.intent;
    const needsMaterials = intent?.needsMaterials ?? true;
    const needsReferenceImage = Boolean(
      intent?.intent === 'propaganda'
      && (intent.suggestedOptions.generateRender || intent.suggestedOptions.generateExplosion || intent.suggestedOptions.enhanceImage)
      && !collectedMaterialsRef.current.referenceAssetId,
    );

    const cardsRef: WorkflowCard[] = [];

    if (needsMaterials || needsReferenceImage) {
      const materialFields = needsReferenceImage
        ? [
            {
              key: 'referenceImage',
              label: '参考图',
              hint: '宣传图、场景融合图、爆炸图、立体效果图都必须上传设计图/商品图/草图后再生成。',
            },
          ]
        : [
            { key: 'referenceImage', label: '参考图', hint: '可上传类似款式或风格参考图' },
            { key: 'outputType', label: '输出类型', hint: '设计图（多视图）/ 2D平面图（基于原图多视图工程图）/ 宣传图 / 爆炸图' },
            { key: 'material', label: '材质', hint: '金属 / 木材 / 塑料 / 陶瓷 / 玻璃等' },
            { key: 'dimension', label: '尺寸', hint: '长宽高 / 重量 / 体积' },
            { key: 'budget', label: '预算', hint: '预期造价范围' },
            { key: 'scene', label: '使用场景', hint: '家居 / 办公 / 户外 / 工业等' },
            { key: 'style', label: '风格', hint: '简约 / 科技感 / 复古 / 高端等' },
            { key: 'feature', label: '特殊功能', hint: '必需的功能或特性' },
            { key: 'brand', label: '品牌规范', hint: '需要遵循的品牌调性或规范' },
          ];
      setMaterialQuestions(materialFields.map((f) => `${f.label}：${f.hint}`));
      setCollectedMaterials(collectedMaterialsRef.current);
      cardsRef.push({
        id: `${ctx.messageId}-materials`,
        type: 'materials_request',
        data: {
          projectName: ctx.projectName,
          fields: materialFields.map((f) => ({ ...f, collected: false })),
          collected: collectedMaterialsRef.current,
          required: needsReferenceImage,
          description: needsReferenceImage
            ? `图上图任务必须先上传参考图。没有参考图时，请先生成「设计图」，再继续做2D平面图、宣传图、场景融合图、爆炸图或立体效果图。`
            : undefined,
        },
      });
      if (needsReferenceImage) {
        cardsRef.push({
          id: `${ctx.messageId}-reference-next`,
          type: 'next_step',
          data: {
            current: 'reference_required',
            recommendations: [
              { label: '没有图，先生成设计图', agent: 'design_agent', icon: 'PencilLine', action: 'design_sheet' },
            ],
          },
        });
      }
    } else {
      cardsRef.push({
        id: `${ctx.messageId}-confirm`,
        type: 'next_step',
        data: {
          current: 'ready_to_generate',
          recommendations: [{ label: '开始生成', agent: 'confirm', icon: '✅', action: 'confirm' }],
        },
      });
    }

    const actionHint = needsMaterials
      ? '请补充设计材料（可逐条回复，也可直接描述）。'
      : needsReferenceImage
        ? '这个任务需要图上图，请先上传参考图；如果没有参考图，可以先生成设计图。'
      : intent?.intent === 'propaganda'
        ? '已识别为宣发需求，将基于参考图生成宣传素材。'
        : '已识别为生产需求，将生成 CAD/图纸。';

    patchMessage(ctx.messageId, {
      text: `项目「${ctx.projectName}」已确认，${actionHint}`,
      cards: cardsRef.length > 0 ? cardsRef : undefined,
    });
    pendingWorkflowRef.current = {
      messageId: ctx.messageId,
      text: ctx.fullRequirement,
      intent: ctx.intent,
      projectId: ctx.projectId,
    };
  }, []);

  const collectMaterial = async (text: string, userMessageId: string, ctx: NonNullable<typeof projectCtxRef.current>) => {
    const assistantMessage: ChatMessage = {
      id: nextId(),
      role: 'assistant',
      text: '正在整理你提供的材料…',
      status: 'running',
    };
    setMessages((prev) => [...prev, assistantMessage]);

    const nextCollected = { ...collectedMaterialsRef.current };
    try {
      const parsed = await agentService.parseMaterials(text);
      for (const [key, value] of Object.entries(parsed)) {
        if (value) nextCollected[key] = value;
      }
      if (parsed.material) nextCollected.material = parsed.material;
      if (parsed.dimension) nextCollected.dimension = parsed.dimension;
      if (parsed.budget) nextCollected.budget = parsed.budget;
      if (parsed.scene) nextCollected.scene = parsed.scene;
      if (parsed.style) nextCollected.style = parsed.style;
      if (parsed.feature) nextCollected.feature = parsed.feature;
      if (parsed.brand) nextCollected.brand = parsed.brand;
    } catch {
      const lower = text.toLowerCase();
      const materialKeywords = ['金属', '木材', '塑料', '陶瓷', '玻璃', '铝合金', '不锈钢', 'abs', '皮革', '布料', '合金'];
      const styleKeywords = ['简约', '科技', '复古', '高端', '现代', '工业风', '北欧', '极简'];
      const dimensionPattern = /(\d+(?:\.\d+)?)\s*(mm|cm|米|厘米|毫米|寸|升|毫升)/i;
      const budgetPattern = /(?:预算|价格|造价|成本)[是为约]?\s*(\d[\d,.]*\s*万?元?)/i;
      if (materialKeywords.some((k) => lower.includes(k))) {
        const found = materialKeywords.find((k) => lower.includes(k));
        if (found) nextCollected.material = found;
      }
      if (styleKeywords.some((k) => lower.includes(k))) {
        const found = styleKeywords.find((k) => lower.includes(k));
        if (found) nextCollected.style = found;
      }
      const dimMatch = text.match(dimensionPattern);
      if (dimMatch) nextCollected.dimension = dimMatch[0];
      const budgetMatch = text.match(budgetPattern);
      if (budgetMatch) nextCollected.budget = budgetMatch[1];
      const sceneKeywords = ['书房', '客厅', '卧室', '办公', '户外', '厨房', '餐厅', '卫浴', '工业', '门店'];
      const sceneFound = sceneKeywords.find((k) => lower.includes(k));
      if (sceneFound) nextCollected.scene = sceneFound;
    }

    if (!Object.keys(nextCollected).some((k) => nextCollected[k])) {
      nextCollected.other = text;
    }
    nextCollected.raw = [nextCollected.raw, text].filter(Boolean).join('；');
    collectedMaterialsRef.current = nextCollected;
    setCollectedMaterials(nextCollected);

    const answeredCount = [
      nextCollected.material,
      nextCollected.style,
      nextCollected.dimension,
      nextCollected.budget,
      nextCollected.scene,
    ].filter(Boolean).length;

    const requirementCard: WorkflowCard = {
      id: `${assistantMessage.id}-requirement`,
      type: 'requirement',
      data: {
        productType: ctx.projectName,
        scene: nextCollected.scene || '待确认',
        style: nextCollected.style || '待确认',
        budget: nextCollected.budget || '待确认',
        dimensions: nextCollected.dimension ? { summary: nextCollected.dimension } : {},
        materials: nextCollected.material ? [nextCollected.material] : [],
        constraints: nextCollected.other ? [nextCollected.other] : [],
        completeness: Math.min(100, 40 + answeredCount * 12),
        missing: [
          !nextCollected.dimension ? '尺寸' : null,
          !nextCollected.material ? '材质' : null,
          !nextCollected.budget ? '预算' : null,
          !nextCollected.scene ? '场景' : null,
          !nextCollected.style ? '风格' : null,
        ].filter((item): item is string => Boolean(item)),
      },
    };

    const remainingQuestions = materialQuestions.filter((q) => {
      const label = q.split('：')[0];
      if (label === '参考图') return false;
      if (label === '材质') return !nextCollected.material;
      if (label === '尺寸') return !nextCollected.dimension;
      if (label === '预算') return !nextCollected.budget;
      if (label === '使用场景') return !nextCollected.scene;
      if (label === '风格') return !nextCollected.style;
      if (label === '特殊功能') return !nextCollected.other;
      return false;
    });

    const messageText = remainingQuestions.length > 0
      ? `已记录：${answeredCount > 0 ? `已补充 ${answeredCount} 项材料。` : '未提取到明确材料。'}\n还有这些可以补充：\n${remainingQuestions.map((q, i) => `${i + 1}. ${q}`).join('\n')}\n\n如果确认需求无需补充，点击下方「确认需求」开始生成。`
      : `材料已收集完整，请确认需求后开始生成设计方案。`;

    const confirmed: ChatMessage = {
      id: assistantMessage.id,
      role: 'assistant',
      text: messageText,
      status: 'completed',
      projectId: ctx.projectId,
      cards: [requirementCard],
    };

    updateMaterialsCard(nextCollected);
    setMessages((prev) => prev.map((m) => (m.id === assistantMessage.id ? confirmed : m)));

    const materialSummary = [
      nextCollected.material ? `材质：${nextCollected.material}` : null,
      nextCollected.dimension ? `尺寸：${nextCollected.dimension}` : null,
      nextCollected.budget ? `预算：${nextCollected.budget}` : null,
      nextCollected.scene ? `使用场景：${nextCollected.scene}` : null,
      nextCollected.style ? `风格：${nextCollected.style}` : null,
      nextCollected.other ? `其他要求：${nextCollected.other}` : null,
      nextCollected.referenceImage ? `参考图：${nextCollected.referenceImage}` : null,
    ].filter(Boolean).join('；');
    const fullRequirementText = [ctx.fullRequirement, materialSummary].filter(Boolean).join('。\n补充：');

    pendingWorkflowRef.current = {
      messageId: assistantMessage.id,
      text: fullRequirementText,
      intent: ctx.intent,
      projectId: ctx.projectId,
    };
    void workspaceMirrorService.safeMirror(conversationIdRef.current, {
      sourceKey: `legacy-requirement:${ctx.projectId}`,
      type: 'requirement',
      status: 'completed',
      title: `${ctx.projectName} · 需求定义`,
      summary: fullRequirementText,
      projectId: ctx.projectId,
      parentSourceKey: `legacy-project:${ctx.projectId}`,
      inputData: { collected: nextCollected },
      uiData: { projection: 'requirement_card' },
    });
    void persistMessage('assistant', confirmed);
  };

  const runWorkflow = async (ctx: NonNullable<typeof pendingWorkflowRef.current>) => {
    const { text, intent, projectId: projectIdValue } = ctx;
    workflowRunningRef.current = true;

    const msgId = nextId();
    const statusId = `workflow-status-${Date.now()}`;

    // 创建一条带状态卡片的助理消息
    const statusCard: WorkflowCard = {
      id: `${statusId}`,
      type: 'status',
      data: { agent: 'design_agent', task: '生成设计方案', progress: 5, stage: '已提交', estimatedRemaining: '约 1-2 分钟' },
    };
    const runMsg: ChatMessage = {
      id: msgId,
      role: 'assistant',
      text: '正在生成设计方案…',
      status: 'running',
      cards: [statusCard],
    };
    setMessages((prev) => [...prev, runMsg]);

    try {
      const referenceAssetId = collectedMaterialsRef.current.referenceAssetId;
      const referenceUrls = [collectedMaterialsRef.current.referenceImage]
        .filter((url): url is string => Boolean(url && url.trim()));
      const payload: IndustrialDesignWorkflowPayload = {
        inputType: 'text',
        text,
        projectName: intent?.projectName || null,
        industry: intent?.industry || '装备制造',
        mode: projectIdValue ? 'redesign' : 'create',
        assetIds: referenceAssetId ? [referenceAssetId] : undefined,
        assetUrls: referenceUrls.length > 0 ? referenceUrls : undefined,
        options: buildWorkflowOptions(intent),
      };

      const task = await createIndustrialDesignWorkflow(payload);
      const updatedStatus: WorkflowCard = {
        id: `${statusId}`,
        type: 'status',
        data: { agent: 'design_agent', task: '生成设计方案', progress: 5, stage: task.currentStep || '已提交', estimatedRemaining: '约 1-2 分钟' },
      };
      patchMessage(msgId, { cards: [updatedStatus] });

      const maxPoll = 120;
      let current = task;
      for (let i = 0; i < maxPoll; i += 1) {
        if (current.status === 'completed' || current.status === 'failed') break;
        await new Promise((resolve) => setTimeout(resolve, 3000));
        current = await getIndustrialDesignWorkflowTask(task.taskId);
        const pollingStatus: WorkflowCard = {
          id: `${statusId}`,
          type: 'status',
          data: { agent: 'design_agent', task: '生成设计方案', progress: current.progress, stage: current.currentStep || '执行中', estimatedRemaining: null },
        };
        patchMessage(msgId, { cards: [pollingStatus] });
      }

      if (current.status === 'failed') throw new Error(current.error || '方案生成失败');

      const outputs = current.outputs || EMPTY_OUTPUTS;
      const renderUrl = getCadAiOutputValue(outputs, ['renderPng', 'enhancedImage']);
      const drawingUrl = getCadAiOutputValue(outputs, ['drawingSvg']);
      const explosionUrl = getCadAiOutputValue(outputs, ['explosionPng']);
      const schemeThumbnails = [renderUrl, explosionUrl, drawingUrl].filter((url): url is string => Boolean(url));
      if (schemeThumbnails.length === 0) {
        throw new Error('设计方案执行完成，但没有返回可预览的图片或图纸结果。');
      }

      // 结果卡片 + 后续步骤卡片
      const resultCard: WorkflowCard = {
        id: `${statusId}-scheme`,
        type: 'design_scheme',
        data: {
          schemeId: `${statusId}-scheme`,
          name: `${intent?.projectName || '设计方案'} · 方案A`,
          thumbnails: schemeThumbnails,
          materials: [],
          estimatedPrice: null,
          renderUrl: renderUrl || null,
          drawingUrl: drawingUrl || null,
          outputs,
        },
      };
      // 更新消息内卡片：只保留结果卡。后续操作在结果卡底部出现一次，避免重复。
      patchMessage(msgId, {
        status: 'completed',
        text: renderUrl || drawingUrl ? '方案已生成，可继续推进后续步骤。' : '方案已生成。',
        cards: [resultCard],
        taskId: current.taskId,
        projectId: current.projectId || projectIdValue,
        versionId: current.versionId,
        outputs,
      });
      promoteOutputsToReference(outputs);
      applyMessageToPreview({ id: msgId, role: 'assistant', text: '方案已生成', status: 'completed', outputs } as ChatMessage);
      if (outputs) setShowPreview(true);
      if ((current.projectId || projectIdValue) && onProjectLinked) onProjectLinked(current.projectId || projectIdValue, projectCtxRef.current?.projectName || projectName || current.projectId || projectIdValue);
      void workspaceMirrorService.safeMirror(conversationIdRef.current, {
        sourceKey: `legacy-render:${current.taskId || statusId}`,
        type: 'render',
        status: 'completed',
        title: `${projectCtxRef.current?.projectName || projectName || '设计项目'} · 设计结果`,
        summary: '方案已生成',
        projectId: current.projectId || projectIdValue || null,
        taskId: current.taskId,
        versionId: current.versionId,
        parentSourceKey: `legacy-project:${current.projectId || projectIdValue}`,
        outputData: { ...outputs },
        uiData: { projection: 'design_scheme_card' },
      });
      void persistMessage('assistant', { id: msgId, role: 'assistant', text: '方案已生成', status: 'completed', taskId: current.taskId, projectId: current.projectId || projectIdValue, versionId: current.versionId, outputs, cards: [resultCard] } as ChatMessage);
    } catch (error) {
      const message = error instanceof Error ? error.message : '生成失败，请稍后重试';
      const errorCard: WorkflowCard = {
        id: `${statusId}-error`,
        type: 'status',
        data: { agent: 'design_agent', task: '生成失败', progress: 0, stage: message, estimatedRemaining: null },
      };
      patchMessage(msgId, { status: 'failed', text: message, error: message, cards: [errorCard] });
      void persistMessage('assistant', { id: msgId, role: 'assistant', text: message, status: 'failed', error: message } as ChatMessage);
    } finally { workflowRunningRef.current = false; }
  };
  runWorkflowRef.current = runWorkflow;

  const showPromptCard = async (ctx: NonNullable<typeof pendingWorkflowRef.current>) => {
    const msgId = nextId();

    // 创建独立消息，附带状态卡片
    const statusCard: WorkflowCard = {
      id: `prompt-status-${Date.now()}`,
      type: 'status',
      data: { agent: 'design_agent', task: '优化提示词', progress: 30, stage: '优化中', estimatedRemaining: '约 10 秒' },
    };
    const msg: ChatMessage = {
      id: msgId,
      role: 'assistant',
      text: '正在优化生成提示词…',
      status: 'running',
      cards: [statusCard],
    };
    setMessages((prev) => [...prev, msg]);

    try {
      const mats = collectedMaterialsRef.current;
      const productName = ctx.intent?.projectName || '';
      const parts: string[] = [];
      if (productName) parts.push(`产品：${productName}`);
      if (ctx.text && ctx.text !== productName) parts.push(ctx.text);
      if (mats.material) parts.push(`材质：${mats.material}`);
      if (mats.dimension) parts.push(`尺寸：${mats.dimension}`);
      if (mats.style) parts.push(`风格：${mats.style}`);
      if (mats.scene) parts.push(`使用场景：${mats.scene}`);
      if (mats.feature) parts.push(`特殊功能：${mats.feature}`);
      if (mats.referenceImage) parts.push('用户已提供参考图');

      const rawPrompt = parts.join('；') || ctx.text;
      const result = await aggregationWorkbenchService.optimizePrompt({ prompt: rawPrompt, model: null });
      const optimizedPrompt = result.data.optimizedPrompt || result.data.finalPrompt || rawPrompt;
      pendingWorkflowRef.current = { ...ctx, text: optimizedPrompt, messageId: msgId };

      // 替换为提示词确认卡
      const promptCard: WorkflowCard = {
        id: `${msgId}-prompt`,
        type: 'prompt_confirm',
        data: { original: ctx.text, optimized: optimizedPrompt, references: result.data.references || [] },
      };
      patchMessage(msgId, {
        status: 'completed',
        text: '提示词已优化，可在卡片中确认或修改。',
        cards: [promptCard],
      });
    } catch {
      pendingWorkflowRef.current = { ...ctx, text: ctx.text, messageId: msgId };
      const promptCard: WorkflowCard = {
        id: `${msgId}-prompt`,
        type: 'prompt_confirm',
        data: { original: ctx.text, optimized: ctx.text, references: [] },
      };
      patchMessage(msgId, {
        status: 'completed',
        text: '提示词准备就绪，可在卡片中确认或修改。',
        cards: [promptCard],
      });
    }
  };

  const triggerNextWorkflow = async (
    actionKind: WorkflowActionKind,
    intent: IntentAnalysis | null,
    projectIdValue: string,
  ) => {
    if (IMAGE_EDIT_ACTIONS.has(actionKind) && !collectedMaterialsRef.current.referenceAssetId) {
      appendReferenceRequiredMessage(actionKind);
      return;
    }
    const nextOptions = buildWorkflowOptions(intent);
    const intentText = intent?.requirementText || '';
    if (actionKind === 'design_sheet') {
      nextOptions.generateCad = false;
      nextOptions.generateThreePreview = false;
      nextOptions.generateDrawing = true;
      nextOptions.generateRender = false;
      nextOptions.generateExplosion = false;
      nextOptions.enhanceImage = false;
      nextOptions.generatePlanLine = false;
    } else if (actionKind === 'plan_2d') {
      nextOptions.generateCad = false;
      nextOptions.generateThreePreview = false;
      nextOptions.generateDrawing = false;
      nextOptions.generateRender = true;
      nextOptions.generateExplosion = false;
      nextOptions.enhanceImage = true;
      nextOptions.generatePlanLine = false;
    } else if (actionKind === '3d') {
      nextOptions.generateCad = false;
      nextOptions.generateThreePreview = false;
      nextOptions.generateDrawing = false;
      nextOptions.generateRender = true;
      nextOptions.generateExplosion = false;
      nextOptions.enhanceImage = true;
      nextOptions.generatePlanLine = false;
    } else if (actionKind === 'cad') {
      nextOptions.generateCad = false;
      nextOptions.generateThreePreview = false;
      nextOptions.generateDrawing = false;
      nextOptions.generateRender = false;
      nextOptions.generateExplosion = false;
      nextOptions.enhanceImage = false;
      nextOptions.generatePlanLine = true;
    } else if (actionKind === 'render') {
      nextOptions.generateRender = true;
      nextOptions.enhanceImage = true;
      nextOptions.generateDrawing = false;
      nextOptions.generateCad = false;
      nextOptions.generateExplosion = false;
      nextOptions.generateThreePreview = false;
      nextOptions.generatePlanLine = false;
    } else if (actionKind === 'scene_fusion') {
      nextOptions.enhanceImage = true;
      nextOptions.generateRender = true;
      nextOptions.generateDrawing = false;
      nextOptions.generateCad = false;
      nextOptions.generateExplosion = false;
      nextOptions.generateThreePreview = false;
      nextOptions.generatePlanLine = false;
    } else if (actionKind === 'explosion') {
      nextOptions.generateExplosion = true;
      nextOptions.generateRender = false;
      nextOptions.generateDrawing = false;
      nextOptions.generateCad = false;
      nextOptions.enhanceImage = true;
      nextOptions.generateThreePreview = false;
      nextOptions.generatePlanLine = false;
    } else if (actionKind === 'quote') {
      nextOptions.generateCad = false;
      nextOptions.generateDrawing = false;
      nextOptions.generateRender = false;
      nextOptions.generateExplosion = false;
      nextOptions.generateThreePreview = false;
    }

    const actionLabel = ACTION_LABELS[actionKind] || actionKind;

    // 创建一条带状态卡片的助理消息
    const msgId = nextId();
    const statusId = `next-status-${Date.now()}`;
    const statusCard: WorkflowCard = {
      id: statusId,
      type: 'status',
      data: { agent: 'design_agent', task: actionLabel, progress: 5, stage: '已提交', estimatedRemaining: '约 1-3 分钟' },
    };
    const runMsg: ChatMessage = {
      id: msgId,
      role: 'assistant',
      text: `正在生成${actionLabel}…`,
      status: 'running',
      cards: [statusCard],
    };
    setMessages((prev) => [...prev, runMsg]);

    try {
      const imageEditModeMap: Record<string, string> = {
        plan_2d: 'plan_2d',
        render: 'poster',
        scene_fusion: 'scene_fusion',
        explosion: 'exploded',
        '3d': 'fake_3d',
      };
      const payload: IndustrialDesignWorkflowPayload = {
        inputType: 'text',
        text: actionKind === 'design_sheet'
          ? `${intentText || intent?.projectName || '产品'}。生成工业设计图：必须包含正视图、后视图、左视图、右视图、上视图、下视图、尺寸标注、材质标注和局部细节，不是单张产品摄影。`
          : actionKind === 'plan_2d'
            ? `${intentText || intent?.projectName || '产品'}。基于原始设计图生成2D多视图平面工程图：同一个物体必须包含正视图、后视图、左侧视图、右侧视图、上视图、下视图、关键尺寸、比例标尺、材料/结构标注；只展示这个物体，不要场景，不要重新设计，不要改变原图主体。`
          : actionKind === '3d'
          ? `${intentText || intent?.projectName || '产品'}。生成二维立体效果图，等轴测或三分之四视角，不生成真实3D网格模型。`
          : actionKind === 'explosion'
            ? `${intentText || intent?.projectName || '产品'}。基于原始设计图生成二维平面爆炸拆解图，必须保留原产品轮廓、比例、结构关系和材质特征。`
          : actionKind === 'scene_fusion'
            ? `${intentText || intent?.projectName || '产品'}。基于原始设计图做场景融合，必须保留原产品主体，只替换环境、光影和摆放场景。`
          : intentText,
        projectName: intent?.projectName || null,
        industry: intent?.industry || '装备制造',
        mode: 'redesign',
        context: imageEditModeMap[actionKind] ? { imageEditMode: imageEditModeMap[actionKind] } : undefined,
        options: nextOptions,
      };
      if (collectedMaterialsRef.current.referenceAssetId) {
        payload.assetIds = [collectedMaterialsRef.current.referenceAssetId];
        payload.assetUrls = [collectedMaterialsRef.current.referenceImage].filter((u): u is string => Boolean(u));
      }
      const task = await createIndustrialDesignWorkflow(payload);
      const updatedStatus: WorkflowCard = {
        id: statusId,
        type: 'status',
        data: { agent: 'design_agent', task: actionLabel, progress: 5, stage: task.currentStep || '任务已提交', estimatedRemaining: '约 1-3 分钟' },
      };
      patchMessage(msgId, { cards: [updatedStatus] });

      let current = task;
      for (let i = 0; i < 120; i += 1) {
        if (current.status === 'completed' || current.status === 'failed') break;
        await new Promise((resolve) => setTimeout(resolve, 3000));
        current = await getIndustrialDesignWorkflowTask(task.taskId);
        const pollingStatus: WorkflowCard = {
          id: statusId,
          type: 'status',
          data: { agent: 'design_agent', task: actionLabel, progress: current.progress, stage: current.currentStep || '执行中', estimatedRemaining: null },
        };
        patchMessage(msgId, { cards: [pollingStatus] });
      }

      if (current.status === 'failed') throw new Error(current.error || '生成失败');

      const outputs = current.outputs || EMPTY_OUTPUTS;
      const modelUrl = getCadAiOutputValue(outputs, ['modelGlb', 'modelStl', 'modelDownloadUrl']);
      const cad2dUrl = getCadAiOutputValue(outputs, ['planLine', 'planLineSvg', 'drawingSvg', 'drawingDxf']);
      const renderUrl = getCadAiOutputValue(outputs, ['renderPng', 'enhancedImage']);

      let resultText = '';
      let resultCard: WorkflowCard | null = null;
      if (actionKind === 'design_sheet' && renderUrl) {
        resultText = '设计图已生成。';
        resultCard = { id: `${statusId}-design-sheet`, type: 'design_scheme', data: { schemeId: `design_sheet`, name: '设计图', thumbnails: [renderUrl], materials: [], estimatedPrice: null, renderUrl, drawingUrl: null, outputs } };
      } else if (actionKind === 'plan_2d' && (renderUrl || cad2dUrl)) {
        resultText = '2D平面图已生成。';
        resultCard = { id: `${statusId}-plan-2d`, type: 'design_scheme', data: { schemeId: `plan_2d`, name: '2D平面图', thumbnails: renderUrl ? [renderUrl] : [], materials: [], estimatedPrice: null, renderUrl: renderUrl || null, drawingUrl: cad2dUrl || null, outputs } };
      } else if (actionKind === '3d' && renderUrl) {
        resultText = '立体效果图已生成。';
        resultCard = { id: `${statusId}-3d`, type: 'design_scheme', data: { schemeId: `3d`, name: '立体效果图', thumbnails: [renderUrl], materials: [], estimatedPrice: null, renderUrl, drawingUrl: null, outputs } };
      } else if (actionKind === 'cad' && cad2dUrl) {
        resultText = '2D CAD 图纸已生成。';
        resultCard = { id: `${statusId}-cad`, type: 'design_scheme', data: { schemeId: `cad`, name: '2D CAD 图纸', thumbnails: [], materials: [], estimatedPrice: null, renderUrl: null, drawingUrl: cad2dUrl || modelUrl, outputs } };
      } else if (actionKind === 'render' && renderUrl) {
        resultText = '宣传图已生成。';
        resultCard = { id: `${statusId}-render`, type: 'design_scheme', data: { schemeId: `render`, name: '宣传图', thumbnails: [renderUrl], materials: [], estimatedPrice: null, renderUrl, drawingUrl: null, outputs } };
      } else if (actionKind === 'scene_fusion' && renderUrl) {
        resultText = '场景融合图已生成。';
        resultCard = { id: `${statusId}-fusion`, type: 'design_scheme', data: { schemeId: `fusion`, name: '场景融合图', thumbnails: [renderUrl], materials: [], estimatedPrice: null, renderUrl, drawingUrl: null, outputs } };
      } else if (actionKind === 'explosion') {
        const explosionUrl = getCadAiOutputValue(outputs, ['explosionPng']);
        resultText = explosionUrl ? '爆炸图已生成。' : '爆炸图生成完成。';
        if (explosionUrl) {
          resultCard = { id: `${statusId}-explosion`, type: 'design_scheme', data: { schemeId: `explosion`, name: '爆炸图', thumbnails: [explosionUrl], materials: [], estimatedPrice: null, renderUrl: explosionUrl, drawingUrl: null, outputs } };
        }
      } else if (actionKind === 'quote') {
        resultText = '报价已生成，请查看详情。';
      } else if (['design_sheet', 'plan_2d', 'render', 'scene_fusion', 'explosion', '3d', 'cad'].includes(actionKind)) {
        throw new Error(`${actionLabel}执行完成，但没有返回有效图片或图纸结果。`);
      } else {
        resultText = '已处理完成。';
      }

      const cards: WorkflowCard[] = resultCard ? [resultCard] : [];

      // 更新消息内卡片
      patchMessage(msgId, {
        status: 'completed',
        text: resultText,
        cards,
        taskId: current.taskId,
        projectId: current.projectId || projectIdValue,
        versionId: current.versionId,
        outputs,
      });
      promoteOutputsToReference(outputs);
      void persistMessage('assistant', { id: msgId, role: 'assistant', text: resultText, status: 'completed', taskId: current.taskId, projectId: current.projectId || projectIdValue, versionId: current.versionId, outputs, cards } as ChatMessage);

      const mirrorType = actionKind === '3d'
        ? 'render'
        : actionKind === 'cad'
          ? 'cad'
          : actionKind === 'quote'
            ? 'quote'
            : 'render';
      void workspaceMirrorService.safeMirror(conversationIdRef.current, {
        sourceKey: `legacy-${mirrorType}:${current.taskId || statusId}`,
        type: mirrorType,
        status: 'completed',
        title: actionLabel,
        summary: resultText,
        projectId: current.projectId || projectIdValue || null,
        taskId: current.taskId,
        versionId: current.versionId,
        parentSourceKey: `legacy-project:${current.projectId || projectIdValue}`,
        outputData: { workflowOutputs: { ...outputs } },
        uiData: { projection: actionKind === 'quote' ? 'quote_card' : 'result_card' },
      });
      if (outputs) {
        applyMessageToPreview({ id: msgId, role: 'assistant', text: resultText, status: 'completed', outputs } as ChatMessage);
        setShowPreview(true);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : '生成失败';
      const errorCard: WorkflowCard = {
        id: `${statusId}-error`,
        type: 'status',
        data: { agent: 'design_agent', task: actionLabel, progress: 0, stage: message, estimatedRemaining: null },
      };
      patchMessage(msgId, { status: 'failed', text: message, error: message, cards: [errorCard] });
      void persistMessage('assistant', { id: msgId, role: 'assistant', text: message, status: 'failed', error: message } as ChatMessage);
    }
  };

  const handleEngineeringPackage = useCallback(async () => {
    const lastTaskMsg = [...messages].reverse().find((m) => m.role === 'assistant' && m.taskId && m.status === 'completed');
    const taskId = lastTaskMsg?.taskId;
    if (!taskId) {
      const msg: ChatMessage = { id: nextId(), role: 'assistant', text: '请先生成设计方案后再导出工程包。', status: 'completed' };
      setMessages((prev) => [...prev, msg]);
      return;
    }
    const statusMsg: ChatMessage = { id: nextId(), role: 'assistant', text: '正在生成工程设计包…', status: 'running' };
    setMessages((prev) => [...prev, statusMsg]);
    try {
      const result = await createEngineeringPackage(taskId);
      patchMessage(statusMsg.id, {
        status: 'completed',
        text: `工程设计包已生成。\n文件名：${result.filename || 'package.zip'}\n[下载](${result.packageDownloadUrl || '#'})`,
      });
      void workspaceMirrorService.safeMirror(conversationIdRef.current, {
        sourceKey: `legacy-package:${taskId}`,
        type: 'engineering_package',
        status: 'completed',
        title: '工程设计包',
        summary: `工程设计包已生成：${result.filename || 'package.zip'}`,
        projectId: projectCtxRef.current?.projectId || projectId || null,
        taskId,
        parentSourceKey: `legacy-project:${projectCtxRef.current?.projectId || projectId}`,
        outputData: {
          filename: result.filename || 'package.zip',
          packageDownloadUrl: result.packageDownloadUrl || '',
        },
        uiData: { projection: 'package_card' },
      });
      void persistMessage('assistant', { id: statusMsg.id, role: 'assistant', text: `工程设计包已生成：${result.filename || 'package.zip'}`, status: 'completed' } as ChatMessage);
    } catch (error) {
      const message = error instanceof Error ? error.message : '工程包导出失败';
      patchMessage(statusMsg.id, { status: 'failed', text: message, error: message });
    }
  }, [messages, projectId]);

  const handleCardAction = useCallback((action: string, data: Record<string, unknown>) => {
    if (action === 'prompt.confirm') {
      const ctx = pendingWorkflowRef.current;
      if (ctx) {
        pendingWorkflowRef.current = null;
        void runWorkflowRef.current?.(ctx);
      }
      return;
    }
    if (action === 'prompt.edit') {
      const prompt = (data.prompt as string) || '';
      if (prompt && pendingWorkflowRef.current) {
        pendingWorkflowRef.current.text = prompt;
      }
      return;
    }
    if (action === 'materials.upload') {
      const file = data.file as File | undefined;
      if (file) void handleFileUpload(file);
      return;
    }
    if (action === 'materials.submit') {
      const values = (data.values as Record<string, string> | undefined) ?? {};
      const nextCollected = { ...collectedMaterialsRef.current, ...values };
      const firstPendingImage = pendingAttachments.find((a) => a.isImage);
      if (firstPendingImage && !nextCollected.referenceImage) {
        nextCollected.referenceImage = firstPendingImage.url;
        nextCollected.referenceAssetId = firstPendingImage.assetId;
      }
      collectedMaterialsRef.current = nextCollected;
      setCollectedMaterials(nextCollected);
      if (pendingAttachments.length > 0) setPendingAttachments([]);
      const ctx = pendingWorkflowRef.current;
      if (ctx) {
        const materialSummary = [
          nextCollected.referenceImage ? '已提供参考图' : null,
          nextCollected.material ? `材质：${nextCollected.material}` : null,
          nextCollected.dimension ? `尺寸：${nextCollected.dimension}` : null,
          nextCollected.budget ? `预算：${nextCollected.budget}` : null,
          nextCollected.scene ? `使用场景：${nextCollected.scene}` : null,
          nextCollected.style ? `风格：${nextCollected.style}` : null,
          nextCollected.feature ? `特殊功能：${nextCollected.feature}` : null,
          nextCollected.brand ? `品牌规范：${nextCollected.brand}` : null,
        ].filter(Boolean).join('；');
        const fullRequirementText = [ctx.text, materialSummary].filter(Boolean).join('。\n补充：');
        confirmedRequirementRef.current.add(ctx.messageId);
        setConfirmedMessages((prev) => (prev.includes(ctx.messageId) ? prev : [...prev, ctx.messageId]));
        pendingWorkflowRef.current = null;
        void showPromptCard({ ...ctx, text: fullRequirementText });
      }
      return;
    }
    if (action === 'materials.done' || action === 'materials.skip') {
      const ctx = pendingWorkflowRef.current;
      if (ctx) {
        confirmedRequirementRef.current.add(ctx.messageId);
        setConfirmedMessages((prev) => (prev.includes(ctx.messageId) ? prev : [...prev, ctx.messageId]));
        pendingWorkflowRef.current = null;
        void showPromptCard(ctx);
      }
      return;
    }
    if (action === 'project.confirm') {
      const ctx = pendingWorkflowRef.current;
      if (!ctx) return;
      confirmedRequirementRef.current.add(ctx.messageId);
      setConfirmedMessages((prev) => (prev.includes(ctx.messageId) ? prev : [...prev, ctx.messageId]));
      pendingWorkflowRef.current = null;
      projectConfirmedRef.current = true;
      showMaterialsStep();
      return;
    }
    if (action === 'project.save') {
      const name = (data.name as string) || '';
      const desc = (data.description as string) || '';
      if (projectCtxRef.current) {
        projectCtxRef.current.projectName = name || projectCtxRef.current.projectName;
        projectCtxRef.current.fullRequirement = desc || projectCtxRef.current.fullRequirement;
      }
      return;
    }
    if (action === 'scheme.preview') {
      const card = messages.flatMap((m) => m.cards ?? []).find((c) => c.id === data.cardId);
      if (card?.type === 'design_scheme') {
        const scheme = card.data as { outputs?: CadAiTaskStatus['outputs'] | null };
        if (scheme.outputs) {
          const renderUrl = normalizePreviewImageSource(
            getCadAiOutputValue(scheme.outputs, ['renderPng', 'enhancedImage']),
          );
          if (renderUrl) {
            setPreviewSource(renderUrl);
            setPreviewKind('image');
            setShowPreview(true);
            return;
          }
          const drawingUrl = normalizePreviewImageSource(
            getCadAiOutputValue(scheme.outputs, ['planLine', 'planLineSvg', 'drawingSvg', 'drawingDxf']),
          );
          if (drawingUrl) {
            setPreviewSource(drawingUrl);
            setPreviewKind('image');
            setShowPreview(true);
          }
        }
        setShowPreview(true);
      }
      return;
    }
    if (action === 'scheme.promote') {
      const promoteAction = data.promote as WorkflowActionKind | undefined;
      if (promoteAction && promoteAction !== 'package') {
        const ctx = pendingWorkflowRef.current || projectCtxRef.current;
        const intent = ctx?.intent ?? null;
        const projectIdValue = ctx?.projectId ?? '';
        void triggerNextWorkflow(promoteAction, intent, projectIdValue);
      }
      if (promoteAction === 'package') {
        void handleEngineeringPackage();
      }
      return;
    }
    if (action === 'next.action') {
      const nextAction = data.nextAction as WorkflowActionKind | undefined;

      if (nextAction === 'confirm') {
        const ctx = pendingWorkflowRef.current;
        if (ctx) {
          confirmedRequirementRef.current.add(ctx.messageId);
          setConfirmedMessages((prev) => (prev.includes(ctx.messageId) ? prev : [...prev, ctx.messageId]));
          pendingWorkflowRef.current = null;
          void showPromptCard(ctx);
        }
        return;
      }

      if (nextAction && ['design_sheet', 'plan_2d', 'render', 'scene_fusion', 'explosion', '3d', 'cad', 'quote'].includes(nextAction)) {
        const ctx = pendingWorkflowRef.current || projectCtxRef.current;
        const intent = ctx?.intent ?? null;
        const projectIdValue = ctx?.projectId ?? '';
        void triggerNextWorkflow(nextAction, intent, projectIdValue);
        return;
      }

      if (nextAction === 'package') {
        void handleEngineeringPackage();
        return;
      }
    }
  }, [messages, pendingAttachments, handleEngineeringPackage]);

  useEffect(() => {
    if (didAutoRun.current || !initialPromptRef.current) return;
    didAutoRun.current = true;
    void handleSend(initialPromptRef.current);
  }, [handleSend]);

  const displayProjectName = projectName || (projectId ? `项目 ${projectId}` : '新对话');

  const loadResourceData = useCallback(async () => {
    try {
      const [assetsRes, historyRes] = await Promise.all([
        assetService.listAll(),
        cocreationHistoryService.listAllHistory(),
      ]);
      const assetItems = (assetsRes.items ?? []).map((a: { id: string; filename: string; kind: string; createdAt: string }) => ({
        id: a.id, url: assetDownloadUrl(a.id), name: a.filename, kind: a.kind, createdAt: a.createdAt,
      }));
      const versionItems = normalizeVersionSnapshots(historyRes.data.snapshots || []).map((v: VersionSnapshot) => ({
        id: v.id, label: v.label, imageUrl: v.previewImageUrl || null, status: v.status, createdAt: v.createdAt || '',
      }));
      setResourceData({ assets: assetItems, versions: versionItems });
    } catch { setResourceData({ assets: [], versions: [] }); }
  }, []);

  useEffect(() => { void loadResourceData(); }, [loadResourceData, messages.length]);

  const resourcePanel = (
    <div className="flex h-full w-[220px] shrink-0 flex-col border-r border-slate-200 bg-[#fafafa]">
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2.5">
        <span className="text-xs font-semibold text-slate-700">资源</span>
        <button type="button" onClick={() => setShowResource(false)} className="flex size-5 items-center justify-center rounded text-slate-400 hover:bg-slate-100">
          <PanelRightClose className="size-3" />
        </button>
      </div>
      <div className="flex border-b border-slate-100">
        {(['assets','versions','files'] as const).map((tab) => (
          <button key={tab} type="button" onClick={() => setResourceTab(tab)}
            className={`flex-1 py-2 text-[11px] font-medium ${resourceTab===tab?'border-b-2 border-purple-500 text-purple-600':'text-slate-400 hover:text-slate-600'}`}>
            {{assets:'资产',versions:'版本',files:'文件'}[tab]}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {resourceTab === 'assets' ? (
          resourceData.assets.length === 0 ? <p className="py-4 text-center text-[11px] text-slate-400">暂无资产</p> :
          <div className="space-y-1.5">{resourceData.assets.slice(0,30).map((a) => (
            <button key={a.id} type="button" onClick={() => {
              const u = normalizePreviewImageSource(a.url);
              if (u) { setPreviewSource(u); setPreviewKind('image'); setPreviewTab('image'); setShowPreview(true); }
            }} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-slate-50">
              {a.kind==='image' ? <span className="flex size-8 shrink-0 items-center justify-center overflow-hidden rounded-md bg-slate-100"><PreviewImage src={a.url} alt={a.name} className="h-full w-full object-cover" /></span>
              : <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-slate-100 text-slate-400"><FileText className="size-3.5" /></span>}
              <span className="min-w-0 flex-1 truncate text-[11px] text-slate-600">{a.name}</span>
            </button>
          ))}</div>
        ) : resourceTab === 'versions' ? (
          resourceData.versions.length === 0 ? <p className="py-4 text-center text-[11px] text-slate-400">暂无版本</p> :
          <div className="space-y-1.5">{resourceData.versions.slice(0,30).map((v) => (
            <button key={v.id} type="button" onClick={() => {
              if(v.imageUrl){const u=normalizePreviewImageSource(v.imageUrl);if(u){setPreviewSource(u);setPreviewKind('image');setPreviewTab('image');setShowPreview(true);}}
            }} className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-slate-50">
              <span className={`flex size-2 shrink-0 rounded-full ${v.status==='completed'||v.status==='已完成'?'bg-emerald-500':v.status==='failed'?'bg-rose-500':'bg-amber-400'}`} />
              <span className="min-w-0 flex-1"><span className="block truncate text-[11px] font-medium text-slate-700">{v.label}</span><span className="text-[10px] text-slate-400">{v.createdAt?.slice(0,10)}</span></span>
              {v.imageUrl?<span className="flex size-8 shrink-0 items-center justify-center overflow-hidden rounded-md bg-slate-100"><PreviewImage src={v.imageUrl} alt={v.label} className="h-full w-full object-cover" /></span>:null}
            </button>
          ))}</div>
        ) : <p className="py-4 text-center text-[11px] text-slate-400">文件功能完善中</p>}
      </div>
    </div>
  );

  /* ── 聊天区（核心） ── */
  const chatPanel = (
    <div className="flex h-full min-w-0 flex-col bg-white">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-slate-200 px-3">
        <span className="text-sm font-medium text-slate-900">{displayProjectName}</span>
        {projectId ? (
          <span className="rounded-full bg-purple-50 px-2 py-0.5 text-[10px] font-medium text-purple-600">
            进行中
          </span>
        ) : null}
        <div className="flex-1" />
        {!externalResourceCenter ? (
          <button
            type="button"
            onClick={() => { setShowResource((prev) => !prev); void loadResourceData(); }}
            className="flex items-center gap-1 rounded px-2 py-1 text-xs text-slate-500 transition-colors hover:bg-slate-50"
          >
            <PanelLeft className="size-3.5" />
            资源
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => setShowPreview((prev) => !prev)}
          className="flex items-center gap-1 rounded px-2 py-1 text-xs text-slate-500 transition-colors hover:bg-slate-50"
        >
          {showPreview ? <PanelRightClose className="size-3.5" /> : <PanelRight className="size-3.5" />}
          预览
        </button>
        <button
          type="button"
          onClick={onNavigateHome}
          className="flex items-center gap-1 rounded px-2 py-1 text-xs text-slate-500 transition-colors hover:bg-slate-50"
        >
          <ArrowLeft className="size-3" />
          首页
        </button>
      </header>

      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <p className="text-sm text-slate-400">描述你的设计需求，开始共创</p>
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-4 px-4 py-4">
            {messages.map((message) => (
              <div
                key={message.id}
                className={`flex gap-3 ${message.role === 'user' ? 'justify-end' : ''}`}
              >
                {message.role === 'assistant' ? (
                  <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-slate-900 text-white">
                    <Sparkles className="size-3.5" />
                  </div>
                ) : null}
                <div className={`min-w-0 max-w-[80%] ${message.role === 'user' ? 'flex flex-col items-end' : ''}`}>
                  <div
                    className={`rounded-2xl px-3.5 py-2.5 text-[13px] leading-5 ${
                      message.role === 'user'
                        ? 'rounded-br-sm bg-slate-900 text-white'
                        : 'rounded-bl-sm border border-slate-200 bg-white text-slate-800'
                    }`}
                  >
                    {message.text}
                    {message.status === 'running' ? (
                      <span className="ml-2 inline-flex items-center gap-1 text-xs text-slate-400">
                        <Loader2 className="size-3 animate-spin" />
                      </span>
                    ) : null}
                  </div>
                  {message.status === 'failed' && message.role === 'assistant' ? (
                    <div className="mt-1.5 flex items-center gap-1 text-[11px] text-rose-500">
                      <XCircle className="size-3" />
                      生成失败
                    </div>
                  ) : null}
                  {message.status === 'completed' && message.role === 'assistant' && message.taskId ? (
                    <div className="mt-1.5 flex items-center gap-1 text-[11px] text-emerald-600">
                      <CheckCircle2 className="size-3" />
                      已完成
                    </div>
                  ) : null}
                  {message.cards && message.cards.length > 0 ? (
                    <MessageCardStack
                      cards={message.cards}
                      onAction={(card, action, extra) => handleCardAction(action, { cardId: card.id, ...extra })}
                    />
                  ) : null}
                </div>
                {message.role === 'user' ? (
                  <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-purple-100 text-purple-600">
                    <MessageSquare className="size-3.5" />
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── 固定工作流卡槽（已移除，卡片统一嵌入消息内） ── */}

      <div className="shrink-0 px-4 pb-4 pt-2">
        {pendingAttachments.length > 0 ? (
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            {pendingAttachments.map((attachment) => (
              <div
                key={attachment.assetId}
                className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2 py-1"
              >
                {attachment.isImage ? (
                  <span className="flex size-6 shrink-0 items-center justify-center overflow-hidden rounded-md bg-slate-100">
                    <PreviewImage src={attachment.url} alt={attachment.name} className="h-full w-full object-cover" />
                  </span>
                ) : (
                  <FileText className="size-3.5 text-slate-400" />
                )}
                <span className="max-w-[120px] truncate text-[11px] text-slate-600">{attachment.name}</span>
                <button
                  type="button"
                  onClick={() => setPendingAttachments((prev) => prev.filter((a) => a.assetId !== attachment.assetId))}
                  className="flex size-4 items-center justify-center rounded text-slate-400 transition hover:bg-slate-100 hover:text-rose-500"
                  aria-label="移除附件"
                >
                  <X className="size-3" />
                </button>
              </div>
            ))}
          </div>
        ) : null}
        <div className="relative rounded-xl border border-slate-200 bg-white px-3 py-2.5 shadow-sm transition-all focus-within:border-slate-300 focus-within:ring-[3px] focus-within:ring-slate-900/5">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                void handleSend();
              }
            }}
            rows={1}
            placeholder="继续输入你的想法，或补充更多信息..."
            className="max-h-32 w-full resize-none bg-transparent pr-20 text-[13px] text-slate-900 outline-none placeholder:text-slate-400"
          />
          <div className="absolute right-2 top-1/2 flex -translate-y-1/2 items-center gap-1">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*,.pdf,.step,.stp,.stl,.glb,.dxf"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = '';
                if (file) void handleFileUpload(file);
              }}
            />
            <button
              type="button"
              disabled={uploading}
              onClick={() => fileInputRef.current?.click()}
              className="flex size-7 items-center justify-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-slate-600 disabled:opacity-40"
              aria-label="上传附件"
            >
              {uploading ? <Loader2 className="size-4 animate-spin" /> : <Paperclip className="size-4" />}
            </button>
            <button type="button" className="flex size-7 items-center justify-center rounded-md text-slate-400 transition hover:bg-slate-100 hover:text-slate-600">
              <Mic className="size-4" />
            </button>
            <button
              type="button"
              onClick={() => void handleSend()}
              disabled={sending || !input.trim()}
              className="flex size-7 items-center justify-center rounded-full bg-slate-900 text-white transition hover:bg-slate-700 disabled:opacity-40"
            >
              {sending ? <Loader2 className="size-3.5 animate-spin" /> : <Send className="size-3.5" />}
            </button>
          </div>
        </div>
        <div className="mt-2 flex items-center justify-center gap-2">
          {EXEC_LEVELS.map((level) => {
            const active = execLevel === level.id;
            return (
              <button
                key={level.id}
                type="button"
                onClick={() => setExecLevel(level.id)}
                className={`flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] transition ${
                  active
                    ? 'border-slate-900 bg-slate-900 text-white'
                    : 'border-slate-200 bg-white text-slate-500 hover:bg-slate-50'
                }`}
              >
                {level.id === 'deep' ? <Boxes className="size-3" /> : <Sparkles className="size-3" />}
                {level.label}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );

  /* ── 预览区（默认隐藏） ── */
  const previewPanel = (
    <div className="flex h-full flex-col bg-white">
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <span className="text-xs font-medium text-slate-500">当前预览</span>
        <button
          type="button"
          onClick={() => setShowPreview(false)}
          className="flex size-6 items-center justify-center rounded text-slate-400 transition hover:bg-slate-100"
          aria-label="关闭预览"
        >
          <PanelRightClose className="size-3.5" />
        </button>
      </div>

      <div className="flex flex-1 items-center justify-center overflow-auto p-4">
        {previewKind === 'stl' && previewSource ? (
          <div className="flex h-full w-full flex-col gap-3">
            <div className="flex-1 overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
              <GeneratedStlPreview downloadUrl={previewSource} />
            </div>
          </div>
        ) : previewSource && previewKind !== 'stl' ? (
          <div className="flex h-full w-full flex-col gap-3">
            <div className="flex flex-1 items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-slate-50 p-4">
              <PreviewImage
                src={previewSource}
                alt="当前预览"
                className="max-h-full max-w-full object-contain"
              />
            </div>
          </div>
        ) : (
          <div className="text-center">
            <p className="text-sm text-slate-500">暂无当前预览</p>
            <p className="mt-1 text-[10px] text-slate-400/70">生成图片或2D图纸后，这里只显示当前结果。</p>
          </div>
        )}
      </div>

      {projectId ? (
        <div className="border-t border-slate-200 px-4 py-2.5">
          <p className="text-[10px] text-slate-400">项目 #{projectId}</p>
        </div>
      ) : null}
    </div>
  );

  return (
    <div ref={bodyRef} className="flex h-full min-w-0 flex-1">
      <ResizablePanel
        left={showResource && !externalResourceCenter ? resourcePanel : null}
        leftWidth={showResource && !externalResourceCenter ? 220 : 0}
        onLeftWidthChange={() => {}}
        center={chatPanel}
        right={showPreview ? previewPanel : null}
        rightWidth={420}
        onRightWidthChange={() => {}}
        minRight={320}
        maxRight={560}
      />
    </div>
  );
};

export type { ChatMessage };
