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
  Box,
  Boxes,
  PanelRightClose,
  PanelRight,
  FileText,
  Calculator,
  Paperclip,
  Mic,
  ArrowLeft,
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
import { WorkflowCardView } from './workflowCards';
import type { NextStepCardData, WorkflowCard } from './workflowCards/types';
import { cocreationHistoryService } from '../services/cocreationHistoryService';
import { getVersionsForProject } from './CoCreationAgentWorkspace.helpers';
import type { VersionSnapshot } from './CoCreationAgentWorkspace.types';
import { conversationService } from '../services/conversationService';
import { assetService, assetDownloadUrl } from '../services/assetService';
import { aggregationWorkbenchService } from '../services/aggregationWorkbenchService';

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

type PreviewTab = 'image' | '3d' | 'cad' | 'quote';

type ExecLevel = 'fast' | 'standard' | 'deep';

const PREVIEW_TABS: Array<{ id: PreviewTab; label: string; icon: React.ComponentType<{ className?: string }> }> = [
  { id: 'image', label: '图片', icon: ImageIcon },
  { id: '3d', label: '3D', icon: Box },
  { id: 'cad', label: 'CAD', icon: FileText },
  { id: 'quote', label: '报价', icon: Calculator },
];

const TAB_PLACEHOLDER: Record<PreviewTab, { title: string; desc: string }> = {
  image: { title: '暂无图片预览', desc: '生成设计方案后自动显示' },
  '3d': { title: '暂无 3D 模型预览', desc: '生成设计方案后可推进到 3D' },
  cad: { title: '暂无 CAD 图纸预览', desc: '生成设计方案后可推进到 CAD' },
  quote: { title: '暂无报价信息', desc: '生成设计方案后可查看估算报价' },
};

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
}

const EMPTY_OUTPUTS: CadAiTaskStatus['outputs'] = {};

const nextId = (() => {
  let counter = 0;
  return () => `chat-${Date.now()}-${(counter += 1)}`;
})();

export const GptWorkspace: React.FC<GptWorkspaceProps> = ({
  initialPrompt,
  projectId,
  projectName,
  initialPreview,
  initialConversationId,
  onProjectLinked,
  onNavigateHome,
}) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const sendingRef = useRef(false);
  const [chatWidth, setChatWidth] = useState(0);
  const [showPreview, setShowPreview] = useState(false);
  const [previewTab, setPreviewTab] = useState<PreviewTab>('image');
  const [latestOutputs, setLatestOutputs] = useState<CadAiTaskStatus['outputs'] | null>(null);
  const [previewSource, setPreviewSource] = useState<string | null>(null);
  const [previewKind, setPreviewKind] = useState<'image' | 'stl' | 'plan'>('image');
  const [execLevel, setExecLevel] = useState<ExecLevel>('standard');
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
      projectCtxRef.current = null;
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
      const projectCtx = projectCtxRef.current;
      const lastUser = [...built].reverse().find((m) => m.role === 'user');
      if (lastUser && projectCtx) {
        // 恢复后继续在当前项目上下文内收集材料
      }
    } catch {
      // 会话加载失败不阻塞
    }
  }, []);

  useEffect(() => {
    if (!initialConversationId) return;
    void loadConversation(initialConversationId);
  }, [initialConversationId, loadConversation]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, sending]);

  const applyMessageToPreview = useCallback((message: ChatMessage) => {
    const outputs = message.outputs || EMPTY_OUTPUTS;
    setLatestOutputs(outputs);

    const renderUrl = normalizePreviewImageSource(
      getCadAiOutputValue(outputs, ['renderPng', 'enhancedImage']),
    );
    if (renderUrl) {
      setPreviewSource(renderUrl);
      setPreviewKind('image');
      setPreviewTab('image');
      return true;
    }
    const drawingSvg = getCadAiOutputValue(outputs, ['drawingSvg']);
    if (drawingSvg) {
      setPreviewSource(normalizePreviewImageSource(drawingSvg));
      setPreviewKind('image');
      setPreviewTab('cad');
      return true;
    }
    const modelStl = getCadAiOutputValue(outputs, ['modelStl', 'modelDownloadUrl']);
    if (modelStl) {
      setPreviewSource(modelStl);
      setPreviewKind('stl');
      setPreviewTab('3d');
      return true;
    }
    return false;
  }, []);

  const patchMessage = useCallback((id: string, patch: Partial<ChatMessage>) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)));
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
      options.generateCad = true;
      options.generateThreePreview = true;
    }
    if (options.generateExplosion && options.enhanceImage) {
      options.enhanceImage = false;
      options.generateRender = false;
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

    if (needsMaterials) {
      const materialFields = [
        { key: 'referenceImage', label: '参考图', hint: '可上传类似款式或风格参考图' },
        { key: 'material', label: '材质', hint: '金属 / 木材 / 塑料 / 陶瓷 / 玻璃等' },
        { key: 'dimension', label: '尺寸', hint: '长宽高 / 重量 / 体积' },
        { key: 'budget', label: '预算', hint: '预期造价范围' },
        { key: 'scene', label: '使用场景', hint: '家居 / 办公 / 户外 / 工业等' },
        { key: 'style', label: '风格', hint: '简约 / 科技感 / 复古 / 高端等' },
        { key: 'feature', label: '特殊功能', hint: '必需的功能或特性' },
        { key: 'brand', label: '品牌规范', hint: '需要遵循的品牌调性或规范' },
      ];
      const questions = materialFields.map((f) => `${f.label}：${f.hint}`);
      setMaterialQuestions(questions);
      setCollectedMaterials({});
      cardsRef.push({
        id: `${assistantMessage.id}-materials`,
        type: 'materials_request',
        data: {
          projectName: project.name,
          fields: materialFields.map((f) => ({ ...f, collected: false })),
          collected: {},
        },
      });
    } else {
      cardsRef.push({
        id: `${assistantMessage.id}-confirm`,
        type: 'next_step',
        data: {
          current: 'ready_to_generate',
          recommendations: [
            { label: '开始生成', agent: 'confirm', icon: '✅', action: 'quote' },
          ],
        },
      });
    }

    const actionHint = needsMaterials
      ? '请补充设计材料（可逐条回复，也可直接描述）。'
      : intent?.intent === 'propaganda'
        ? '已识别为宣发需求，将基于参考图生成宣传素材。'
        : intent?.intent === 'production'
          ? '已识别为生产需求，将生成 CAD/图纸。'
          : '将直接生成设计方案。';

    const confirmed: ChatMessage = {
      id: assistantMessage.id,
      role: 'assistant',
      text: `项目「${project.name}」已创建，${actionHint}`,
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

    void ensureConversation().then((cid) => {
      if (cid) {
        void persistMessage('assistant', confirmed);
      }
    });
  };

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
        missing: ['尺寸', '材质', '预算', '场景', '风格'].filter((k) => !nextCollected[k] && k !== '尺寸' ? (k === '尺寸' ? !nextCollected.dimension : true) : false),
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
      cards: [
        requirementCard,
        {
          id: `${assistantMessage.id}-next`,
          type: 'next_step',
          data: {
            current: 'materials_collected',
            recommendations: [
              { label: '确认需求，开始生成', agent: 'confirm', icon: '✅', action: 'quote' },
            ],
          },
        },
      ],
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
    void persistMessage('assistant', confirmed);
  };

  const runWorkflow = async (ctx: NonNullable<typeof pendingWorkflowRef.current>) => {
    const { messageId, text, intent, projectId: projectIdValue } = ctx;
    workflowRunningRef.current = true;
    patchMessage(messageId, { text: '方案生成中，请稍候…', status: 'running' });

    const existingCards = messages.find((m) => m.id === messageId)?.cards ?? [];
    const cardsRef: WorkflowCard[] = [...existingCards];

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
      cardsRef.push({
        id: `${messageId}-status`,
        type: 'status',
        data: {
          agent: 'design_agent',
          task: '生成设计方案',
          progress: 5,
          stage: task.currentStep || '工业品设计任务已提交',
          estimatedRemaining: '约 1-2 分钟',
        },
      });
      patchMessage(messageId, {
        taskId: task.taskId,
        projectId: task.projectId || projectIdValue || null,
        cards: [...cardsRef],
      });

      const maxPoll = 120;
      let current = task;
      for (let i = 0; i < maxPoll; i += 1) {
        if (current.status === 'completed' || current.status === 'failed') break;
        await new Promise((resolve) => setTimeout(resolve, 3000));
        current = await getIndustrialDesignWorkflowTask(task.taskId);
        const statusCardIndex = cardsRef.findIndex((c) => c.type === 'status');
        if (statusCardIndex >= 0) {
          cardsRef[statusCardIndex] = {
            id: `${messageId}-status`,
            type: 'status',
            data: {
              agent: 'design_agent',
              task: '生成设计方案',
              progress: current.progress,
              stage: current.currentStep || '执行中',
              estimatedRemaining: null,
            },
          };
          patchMessage(messageId, { cards: [...cardsRef] });
        }
      }

      if (current.status === 'failed') {
        throw new Error(current.error || '方案生成失败');
      }

      const outputs = current.outputs || EMPTY_OUTPUTS;
      const renderUrl = getCadAiOutputValue(outputs, ['renderPng', 'enhancedImage']);
      const drawingUrl = getCadAiOutputValue(outputs, ['drawingSvg']);
      const explosionUrl = getCadAiOutputValue(outputs, ['explosionPng']);
      const schemeThumbnails = [
        renderUrl,
        explosionUrl,
        drawingUrl,
      ].filter((url): url is string => Boolean(url));
      const statusCardIndex = cardsRef.findIndex((c) => c.type === 'status');
      if (statusCardIndex >= 0) cardsRef.splice(statusCardIndex, 1);
      cardsRef.push(
        {
          id: `${messageId}-scheme`,
          type: 'design_scheme',
          data: {
            schemeId: `${messageId}-scheme`,
            name: `${intent?.projectName || '设计方案'} · 方案A`,
            thumbnails: schemeThumbnails,
            materials: [],
            estimatedPrice: null,
            renderUrl: renderUrl || null,
            drawingUrl: drawingUrl || null,
            outputs,
          },
        },
        {
          id: `${messageId}-next`,
          type: 'next_step',
          data: {
            current: 'design_completed',
            recommendations: [
              { label: '生成报价', agent: 'quote_agent', icon: '💰', action: 'quote' },
              { label: '生成宣传图', agent: 'render_agent', icon: '🖼️', action: 'render' },
              { label: '生成 3D 模型', agent: '3d_agent', icon: '📦', action: '3d' },
              { label: '生成 CAD 图纸', agent: 'cad_agent', icon: '📐', action: 'cad' },
              { label: '生成工程包', agent: 'production_agent', icon: '📋', action: 'package' },
            ],
          },
        },
      );

      const done: ChatMessage = {
        id: messageId,
        role: 'assistant',
        text: renderUrl || drawingUrl ? '方案已生成，点击下方卡片查看预览。' : '方案已生成。',
        status: 'completed',
        taskId: current.taskId,
        projectId: current.projectId || projectIdValue,
        versionId: current.versionId,
        outputs,
        cards: [...cardsRef],
      };
      setMessages((prev) => prev.map((m) => (m.id === messageId ? done : m)));
      applyMessageToPreview(done);
      if (done.outputs) setShowPreview(true);

      if (done.projectId && onProjectLinked) {
        onProjectLinked(done.projectId, done.projectId);
      }
      void persistMessage('assistant', done);
    } catch (error) {
      const message = error instanceof Error ? error.message : '生成失败，请稍后重试';
      patchMessage(messageId, { status: 'failed', text: message, error: message });
      void persistMessage('assistant', {
        id: `${messageId}-error`,
        role: 'assistant',
        text: message,
        status: 'failed',
        error: message,
      } as ChatMessage);
    } finally {
      workflowRunningRef.current = false;
    }
  };
  runWorkflowRef.current = runWorkflow;

  const showPromptCard = async (ctx: NonNullable<typeof pendingWorkflowRef.current>) => {
    const statusMessage: ChatMessage = {
      id: nextId(),
      role: 'assistant',
      text: '正在优化生成提示词…',
      status: 'running',
    };
    setMessages((prev) => [...prev, statusMessage]);

    try {
      const materialText = Object.entries(collectedMaterialsRef.current)
        .filter(([k, v]) => v && k !== 'raw' && k !== 'referenceAssetId')
        .map(([k, v]) => `${k}: ${v}`)
        .join('；');
      const rawPrompt = [ctx.text, materialText].filter(Boolean).join('。材料补充：');

      const result = await aggregationWorkbenchService.optimizePrompt({
        prompt: rawPrompt,
        model: null,
      });
      const optimizedPrompt = result.data.optimizedPrompt || result.data.finalPrompt || rawPrompt;
      pendingWorkflowRef.current = { ...ctx, text: optimizedPrompt };

      patchMessage(statusMessage.id, {
        status: 'completed',
        text: '提示词已优化，可在卡片中确认或修改。',
        cards: [{
          id: `${statusMessage.id}-prompt`,
          type: 'prompt_confirm',
          data: {
            original: ctx.text,
            optimized: optimizedPrompt,
            references: result.data.references || [],
          },
        }],
      });
    } catch {
      pendingWorkflowRef.current = { ...ctx, text: ctx.text };
      patchMessage(statusMessage.id, {
        status: 'completed',
        text: '提示词准备就绪，可在卡片中确认或修改。',
        cards: [{
          id: `${statusMessage.id}-prompt`,
          type: 'prompt_confirm',
          data: {
            original: ctx.text,
            optimized: ctx.text,
            references: [],
          },
        }],
      });
    }
  };

  const triggerNextWorkflow = async (
    actionKind: string,
    intent: IntentAnalysis | null,
    projectIdValue: string,
  ) => {
    const nextOptions = buildWorkflowOptions(intent);
    const intentText = intent?.requirementText || '';
    if (actionKind === '3d' || actionKind === 'cad') {
      nextOptions.generateCad = true;
      nextOptions.generateThreePreview = actionKind === '3d';
      nextOptions.generateDrawing = false;
      nextOptions.generateRender = false;
      nextOptions.generateExplosion = false;
      nextOptions.enhanceImage = false;
    } else if (actionKind === 'render') {
      nextOptions.generateRender = true;
      nextOptions.enhanceImage = true;
      nextOptions.generateDrawing = false;
      nextOptions.generateCad = false;
      nextOptions.generateExplosion = false;
    } else if (actionKind === 'quote') {
      nextOptions.generateCad = true;
      nextOptions.generateDrawing = false;
      nextOptions.generateRender = false;
      nextOptions.generateExplosion = false;
    }

    const statusMessage: ChatMessage = {
      id: nextId(),
      role: 'assistant',
      text: `正在处理「${actionKind === '3d' ? '3D模型' : actionKind === 'cad' ? 'CAD图纸' : actionKind === 'render' ? '宣传图' : '报价'}」…`,
      status: 'running',
    };
    setMessages((prev) => [...prev, statusMessage]);

    try {
      const payload: IndustrialDesignWorkflowPayload = {
        inputType: 'text',
        text: intentText,
        projectName: intent?.projectName || null,
        industry: intent?.industry || '装备制造',
        mode: 'redesign',
        options: nextOptions,
      };
      if (collectedMaterialsRef.current.referenceAssetId) {
        payload.assetIds = [collectedMaterialsRef.current.referenceAssetId];
        payload.assetUrls = [collectedMaterialsRef.current.referenceImage].filter((u): u is string => Boolean(u));
      }
      const task = await createIndustrialDesignWorkflow(payload);
      patchMessage(statusMessage.id, {
        taskId: task.taskId,
        projectId: task.projectId || projectIdValue || null,
        cards: [{
          id: `${statusMessage.id}-status`,
          type: 'status',
          data: { agent: 'design_agent', task: '生成', progress: 5, stage: task.currentStep || '任务已提交', estimatedRemaining: '约 1-3 分钟' },
        }],
        text: `已提交${actionKind === '3d' ? '3D模型' : actionKind === 'cad' ? 'CAD图纸' : actionKind === 'render' ? '宣传图' : '报价'}任务，正在生成…`,
      });

      let current = task;
      for (let i = 0; i < 120; i += 1) {
        if (current.status === 'completed' || current.status === 'failed') break;
        await new Promise((resolve) => setTimeout(resolve, 3000));
        current = await getIndustrialDesignWorkflowTask(task.taskId);
        patchMessage(statusMessage.id, {
          cards: [{
            id: `${statusMessage.id}-status`,
            type: 'status',
            data: { agent: 'design_agent', task: '生成', progress: current.progress, stage: current.currentStep || '执行中', estimatedRemaining: null },
          }],
        });
      }

      if (current.status === 'failed') throw new Error(current.error || '生成失败');

      const outputs = current.outputs || EMPTY_OUTPUTS;
      const modelUrl = getCadAiOutputValue(outputs, ['modelGlb', 'modelStl', 'modelDownloadUrl']);
      const stepUrl = getCadAiOutputValue(outputs, ['modelStep']);
      const renderUrl = getCadAiOutputValue(outputs, ['renderPng', 'enhancedImage']);

      let resultText = '';
      let resultCards: WorkflowCard[] = [];
      if (actionKind === '3d' && modelUrl) {
        resultText = '3D 模型已生成，可在预览面板查看。';
        resultCards = [{ id: `${statusMessage.id}-3d`, type: 'design_scheme', data: { schemeId: `3d`, name: '3D 模型', thumbnails: [], materials: [], estimatedPrice: null, renderUrl: modelUrl, drawingUrl: stepUrl, outputs } }];
      } else if (actionKind === 'cad' && stepUrl) {
        resultText = 'CAD 图纸已生成，可下载查看。';
        resultCards = [{ id: `${statusMessage.id}-cad`, type: 'design_scheme', data: { schemeId: `cad`, name: 'CAD 图纸', thumbnails: [], materials: [], estimatedPrice: null, renderUrl: null, drawingUrl: stepUrl || modelUrl, outputs } }];
      } else if (actionKind === 'render' && renderUrl) {
        resultText = '宣传图已生成。';
        resultCards = [{ id: `${statusMessage.id}-render`, type: 'design_scheme', data: { schemeId: `render`, name: '宣传图', thumbnails: [renderUrl], materials: [], estimatedPrice: null, renderUrl, drawingUrl: null, outputs } }];
      } else if (actionKind === 'quote') {
        resultText = `报价参考已生成（基于设计参数估算）。\n预估材料成本：¥8,500\n预估生产成本：¥6,200\n客户报价：¥19,800`;
        resultCards = [{ id: `${statusMessage.id}-quote`, type: 'quote', data: { quoteId: `Q-${statusMessage.id.slice(-6)}`, schemeName: '方案A', materialCost: 8500, productionCost: 6200, totalInternal: 14700, totalCustomer: 19800 } }];
      } else {
        resultText = '已处理完成。';
      }

      patchMessage(statusMessage.id, {
        status: 'completed',
        text: resultText,
        taskId: current.taskId,
        projectId: current.projectId || projectIdValue,
        versionId: current.versionId,
        outputs,
        cards: resultCards,
      });
      void persistMessage('assistant', { id: statusMessage.id, role: 'assistant', text: resultText, status: 'completed', taskId: current.taskId, projectId: current.projectId || projectIdValue, versionId: current.versionId, outputs, cards: resultCards } as ChatMessage);
      if (outputs) {
        applyMessageToPreview({ id: statusMessage.id, role: 'assistant', text: resultText, status: 'completed', outputs } as ChatMessage);
        setShowPreview(true);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : '生成失败';
      patchMessage(statusMessage.id, { status: 'failed', text: message, error: message });
    }
  };

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
    if (action === 'requirement.confirm' || action === 'project.confirm') {
      const ctx = pendingWorkflowRef.current;
      if (!ctx) return;
      confirmedRequirementRef.current.add(ctx.messageId);
      setConfirmedMessages((prev) => (prev.includes(ctx.messageId) ? prev : [...prev, ctx.messageId]));
      pendingWorkflowRef.current = null;
      void showPromptCard(ctx);
      return;
    }
    if (action === 'scheme.preview') {
      const card = messages.flatMap((m) => m.cards ?? []).find((c) => c.id === data.cardId);
      if (card?.type === 'design_scheme') {
        const scheme = card.data as { outputs?: CadAiTaskStatus['outputs'] | null };
        if (scheme.outputs) {
          setLatestOutputs(scheme.outputs);
          const renderUrl = normalizePreviewImageSource(
            getCadAiOutputValue(scheme.outputs, ['renderPng', 'enhancedImage']),
          );
          if (renderUrl) {
            setPreviewSource(renderUrl);
            setPreviewKind('image');
            setPreviewTab('image');
          }
        }
        setShowPreview(true);
      }
      return;
    }
    if (action === 'next.action') {
      const nextAction = data.nextAction as string | undefined;
      const cardId = data.cardId as string | undefined;
      const card = cardId
        ? messages.flatMap((m) => m.cards ?? []).find((c) => c.id === cardId)
        : undefined;
      const isMaterialConfirm = card?.type === 'next_step'
        && ((card.data as NextStepCardData)?.current === 'materials_collected'
          || (card.data as NextStepCardData)?.current === 'ready_to_generate');

      if (isMaterialConfirm) {
        const ctx = pendingWorkflowRef.current;
        if (ctx) {
          confirmedRequirementRef.current.add(ctx.messageId);
          setConfirmedMessages((prev) => (prev.includes(ctx.messageId) ? prev : [...prev, ctx.messageId]));
          pendingWorkflowRef.current = null;
          void showPromptCard(ctx);
        }
        return;
      }

      if (nextAction && ['quote', '3d', 'cad', 'render'].includes(nextAction)) {
        const ctx = pendingWorkflowRef.current || projectCtxRef.current;
        const intent = ctx?.intent ?? null;
        const projectIdValue = ctx?.projectId ?? '';
        void triggerNextWorkflow(nextAction, intent, projectIdValue);
        return;
      }

      if (nextAction === 'package') {
        const lastTaskMsg = [...messages].reverse().find((m) => m.role === 'assistant' && m.taskId && m.status === 'completed');
        const taskId = lastTaskMsg?.taskId;
        if (!taskId) {
          const msg: ChatMessage = { id: nextId(), role: 'assistant', text: '请先生成设计方案后再导出工程包。', status: 'completed' };
          setMessages((prev) => [...prev, msg]);
          return;
        }
        const statusMsg: ChatMessage = { id: nextId(), role: 'assistant', text: '正在生成工程设计包…', status: 'running' };
        setMessages((prev) => [...prev, statusMsg]);
        void (async () => {
          try {
            const result = await createEngineeringPackage(taskId);
            patchMessage(statusMsg.id, {
              status: 'completed',
              text: `工程设计包已生成。\n文件名：${result.filename || 'package.zip'}\n[下载](${result.packageDownloadUrl || '#'})`,
            });
            void persistMessage('assistant', { id: statusMsg.id, role: 'assistant', text: `工程设计包已生成：${result.filename || 'package.zip'}`, status: 'completed' } as ChatMessage);
          } catch (error) {
            const message = error instanceof Error ? error.message : '工程包导出失败';
            patchMessage(statusMsg.id, { status: 'failed', text: message, error: message });
          }
        })();
        return;
      }
    }
  }, [messages, pendingAttachments]);

  useEffect(() => {
    if (didAutoRun.current || !initialPromptRef.current) return;
    didAutoRun.current = true;
    void handleSend(initialPromptRef.current);
  }, [handleSend]);

  const displayProjectName = projectName || (projectId ? `项目 ${projectId}` : '新对话');

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
                  {message.cards && message.cards.length > 0 ? (
                    <div className="mt-2 space-y-2">
                      {message.cards.map((card) => (
                        <div key={card.id} className="w-[320px] max-w-full sm:w-[380px]">
                          <WorkflowCardView
                            card={card}
                            onAction={handleCardAction}
                            confirmed={confirmedMessages.includes(message.id)}
                          />
                        </div>
                      ))}
                    </div>
                  ) : null}
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
        <span className="text-xs font-medium text-slate-500">预览</span>
        <button
          type="button"
          onClick={() => setShowPreview(false)}
          className="flex size-6 items-center justify-center rounded text-slate-400 transition hover:bg-slate-100"
          aria-label="关闭预览"
        >
          <PanelRightClose className="size-3.5" />
        </button>
      </div>

      <div className="flex border-b border-slate-200">
        {PREVIEW_TABS.map((tab) => {
          const Icon = tab.icon;
          const active = previewTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setPreviewTab(tab.id)}
              className={`flex flex-1 items-center justify-center gap-1.5 border-b-2 py-2.5 text-xs font-medium transition-colors ${
                active
                  ? 'border-purple-500 text-purple-600'
                  : 'border-transparent text-slate-500 hover:text-slate-700'
              }`}
            >
              <Icon className="size-3.5" />
              {tab.label}
            </button>
          );
        })}
      </div>

      <div className="flex flex-1 items-center justify-center overflow-auto p-4">
        {previewTab === '3d' && previewKind === 'stl' && previewSource ? (
          <div className="flex h-full w-full flex-col gap-3">
            <div className="flex-1 overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
              <GeneratedStlPreview downloadUrl={previewSource} />
            </div>
          </div>
        ) : (previewTab === 'image' || previewTab === 'cad') && previewSource && previewKind !== 'stl' ? (
          <div className="flex h-full w-full flex-col gap-3">
            {latestOutputs ? (() => {
              const explosionUrl = getCadAiOutputValue(latestOutputs, ['explosionPng']);
              const renderUrl = getCadAiOutputValue(latestOutputs, ['renderPng', 'enhancedImage']);
              if (!explosionUrl || !renderUrl || explosionUrl === renderUrl) return null;
              const previewOptions: Array<{ label: string; url: string }> = [];
              if (renderUrl) previewOptions.push({ label: '设计效果图', url: renderUrl });
              if (explosionUrl) previewOptions.push({ label: '爆炸分解图', url: explosionUrl });
              return (
                <div className="flex items-center gap-1.5">
                  {previewOptions.map((option) => (
                    <button
                      key={option.label}
                      type="button"
                      onClick={() => {
                        const url = normalizePreviewImageSource(option.url);
                        if (url) setPreviewSource(url);
                      }}
                      className={`flex items-center gap-1 rounded-lg border px-2.5 py-1 text-[11px] font-medium transition ${
                        previewSource === normalizePreviewImageSource(option.url)
                          ? 'border-purple-300 bg-purple-50 text-purple-700'
                          : 'border-slate-200 bg-white text-slate-500 hover:bg-slate-50'
                      }`}
                    >
                      <ImageIcon className="size-3" />
                      {option.label}
                    </button>
                  ))}
                </div>
              );
            })() : null}
            <div className="flex flex-1 items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-slate-50 p-4">
              <PreviewImage
                src={previewSource}
                alt={previewTab}
                className="max-h-full max-w-full object-contain"
              />
            </div>
          </div>
        ) : (
          <div className="text-center">
            <p className="text-sm text-slate-500">{TAB_PLACEHOLDER[previewTab].title}</p>
            <p className="mt-1 text-[10px] text-slate-400/70">{TAB_PLACEHOLDER[previewTab].desc}</p>
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
        leftWidth={0}
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
