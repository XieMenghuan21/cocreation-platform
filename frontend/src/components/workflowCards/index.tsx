import React, { useRef } from 'react';
import {
  Check,
  FolderKanban,
  Sparkles,
  Loader2,
  Wand2,
  Palette,
  Calculator,
  Box,
  Boxes,
  FileText,
  Package,
  Image as ImageIcon,
  PencilLine,
  ArrowRight,
  Ruler,
  Wallet,
  Layers,
  ClipboardCheck,
} from 'lucide-react';
import PreviewImage from '../PreviewImage';
import { normalizePreviewImageSource } from '../../utils/previewImage';
import type {
  CardDataByType,
  DesignSchemeCardData,
  MaterialsRequestCardData,
  NextStepCardData,
  ProjectCreatedCardData,
  PromptCardData,
  QuoteCardData,
  RequirementCardData,
  StatusCardData,
  WorkflowCard,
} from './types';

/* ── 卡片外壳 ── */

interface CardShellProps {
  icon: React.ReactNode;
  accent: 'purple' | 'amber' | 'emerald' | 'sky' | 'rose' | 'slate';
  title: string;
  children: React.ReactNode;
  actions?: React.ReactNode;
}

const ACCENTS: Record<CardShellProps['accent'], { border: string; iconBg: string; iconText: string; badge: string }> = {
  purple: { border: 'border-purple-200', iconBg: 'bg-purple-100', iconText: 'text-purple-600', badge: 'bg-purple-50 text-purple-700' },
  amber: { border: 'border-amber-200', iconBg: 'bg-amber-100', iconText: 'text-amber-600', badge: 'bg-amber-50 text-amber-700' },
  emerald: { border: 'border-emerald-200', iconBg: 'bg-emerald-100', iconText: 'text-emerald-600', badge: 'bg-emerald-50 text-emerald-700' },
  sky: { border: 'border-sky-200', iconBg: 'bg-sky-100', iconText: 'text-sky-600', badge: 'bg-sky-50 text-sky-700' },
  rose: { border: 'border-rose-200', iconBg: 'bg-rose-100', iconText: 'text-rose-600', badge: 'bg-rose-50 text-rose-700' },
  slate: { border: 'border-slate-200', iconBg: 'bg-slate-100', iconText: 'text-slate-600', badge: 'bg-slate-100 text-slate-700' },
};

export const CardShell: React.FC<CardShellProps> = ({ icon, accent, title, children, actions }) => {
  const theme = ACCENTS[accent];
  return (
    <div className={`overflow-hidden rounded-xl border ${theme.border} bg-white shadow-sm`}>
      <div className="flex items-center gap-2 border-b border-slate-100 px-3.5 py-2.5">
        <span className={`flex size-6 items-center justify-center rounded-md ${theme.iconBg} ${theme.iconText}`}>
          {icon}
        </span>
        <span className="text-xs font-semibold text-slate-800">{title}</span>
      </div>
      <div className="px-3.5 py-3">{children}</div>
      {actions ? <div className="flex flex-wrap items-center gap-1.5 border-t border-slate-100 px-3.5 py-2.5">{actions}</div> : null}
    </div>
  );
};

/* ── 通用小按钮 ── */

const CardAction: React.FC<{
  onClick: () => void;
  children: React.ReactNode;
  primary?: boolean;
  disabled?: boolean;
}> = ({ onClick, children, primary, disabled }) => (
  <button
    type="button"
    disabled={disabled}
    onClick={onClick}
    className={`flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium transition ${
      disabled
        ? 'cursor-not-allowed border border-slate-200 bg-slate-100 text-slate-400'
        : primary
        ? 'bg-slate-900 text-white hover:bg-slate-700'
        : 'border border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50'
    }`}
  >
    {children}
  </button>
);

const TagPill: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] text-slate-600">
    {children}
  </span>
);

/* ── 4.1 项目创建卡片 ── */

export const ProjectCreatedCard: React.FC<{
  data: ProjectCreatedCardData;
  onConfirm: (name?: string, desc?: string) => void;
  onEdit: () => void;
}> = ({ data, onConfirm, onEdit }) => {
  const [editing, setEditing] = React.useState(false);
  const [editName, setEditName] = React.useState(data.name);
  const [editDesc, setEditDesc] = React.useState(data.description);

  if (editing) {
    return (
      <CardShell icon={<FolderKanban className="size-3.5" />} accent="purple" title="修改项目">
        <div className="space-y-2">
          <input type="text" value={editName} onChange={(e) => setEditName(e.target.value)}
            className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs outline-none focus:border-purple-300" placeholder="项目名称" />
          <textarea value={editDesc} onChange={(e) => setEditDesc(e.target.value)} rows={2}
            className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs outline-none focus:border-purple-300" placeholder="项目描述" />
        </div>
        <div className="mt-2 flex items-center gap-1.5">
          <CardAction onClick={() => { setEditing(false); onConfirm(editName, editDesc); }} primary>保存</CardAction>
          <CardAction onClick={() => setEditing(false)}>取消</CardAction>
        </div>
      </CardShell>
    );
  }

  return (
    <CardShell icon={<FolderKanban className="size-3.5" />} accent="purple" title="项目已创建">
      <div className="space-y-1.5">
        <div className="text-sm font-semibold text-slate-900">{data.name}</div>
        <p className="text-xs leading-5 text-slate-500">{data.description}</p>
        <div className="flex items-center gap-1.5 pt-1">
          <TagPill>{data.projectType || '工业设计'}</TagPill>
        </div>
      </div>
      <div className="mt-1 flex items-center gap-1.5">
        <CardAction onClick={() => onConfirm()} primary>确认项目</CardAction>
        <CardAction onClick={() => { setEditing(true); onEdit(); }}>修改</CardAction>
      </div>
    </CardShell>
  );
};

/* ── 4.2 需求卡片 ── */

export const RequirementCard: React.FC<{
  data: RequirementCardData;
  onConfirm: () => void;
  onEdit: () => void;
}> = ({ data, onConfirm, onEdit }) => {
  const rows: Array<{ label: string; value: string }> = [
    { label: '产品类型', value: data.productType },
    { label: '场景', value: data.scene },
    { label: '风格', value: data.style },
  ];
  if (data.budget) rows.push({ label: '预算', value: data.budget });
  return (
    <CardShell icon={<ClipboardCheck className="size-3.5" />} accent="sky" title="结构化需求">
      <div className="space-y-1.5">
        {rows.map((row) => (
          <div key={row.label} className="flex items-start gap-2 text-xs">
            <span className="w-14 shrink-0 text-slate-400">{row.label}</span>
            <span className="flex-1 text-slate-800">{row.value}</span>
          </div>
        ))}
        {data.materials.length > 0 ? (
          <div className="flex items-start gap-2 text-xs">
            <span className="w-14 shrink-0 text-slate-400">材质</span>
            <span className="flex-1 flex flex-wrap gap-1">
              {data.materials.map((m) => <TagPill key={m}>{m}</TagPill>)}
            </span>
          </div>
        ) : null}
        <div className="flex items-center gap-2 pt-1">
          <span className="rounded-full bg-sky-50 px-2 py-0.5 text-[11px] font-medium text-sky-700">
            需求完整度 {data.completeness}%
          </span>
          {data.missing.length > 0 ? (
            <span className="text-[11px] text-slate-400">待补充：{data.missing.join('、')}</span>
          ) : null}
        </div>
      </div>
      <div className="mt-2 flex items-center gap-1.5">
        <CardAction onClick={onConfirm} primary>确认需求</CardAction>
        <CardAction onClick={onEdit}>修改</CardAction>
      </div>
    </CardShell>
  );
};

/* ── 4.3 设计方案卡片 ── */

export const DesignSchemeCard: React.FC<{
  data: DesignSchemeCardData;
  onPreview: () => void;
  onPromote: (action: NextStepCardData['recommendations'][number]['action']) => void;
}> = ({ data, onPreview, onPromote }) => {
  const drawingPreview = normalizePreviewImageSource(data.drawingUrl || '');
  const thumbnails = [
    ...(data.thumbnails ?? []),
    ...(drawingPreview ? [drawingPreview] : []),
  ]
    .map((url) => normalizePreviewImageSource(url))
    .filter((url): url is string => Boolean(url));
  const labels = data.outputs
    ? [
        (data.outputs as { renderPng?: string }).renderPng ? '设计效果图' : null,
        (data.outputs as { explosionPng?: string }).explosionPng ? '爆炸分解图' : null,
        ((data.outputs as { drawingSvg?: string }).drawingSvg || (data.outputs as { planLine?: string }).planLine) ? '2D 平面图' : null,
      ].filter(Boolean)
    : [];
  const [activeIndex, setActiveIndex] = React.useState(0);
  const activeThumb = thumbnails[Math.min(activeIndex, thumbnails.length - 1)] ?? null;
  return (
    <CardShell icon={<Palette className="size-3.5" />} accent="purple" title={data.name}>
      <div className="space-y-2.5">
        {activeThumb ? (
          <div className="overflow-hidden rounded-lg border border-slate-100 bg-slate-50">
            <PreviewImage src={activeThumb} alt={data.name} className="h-40 w-full object-cover" />
          </div>
        ) : null}
        {thumbnails.length > 1 ? (
          <div className="flex flex-wrap gap-1.5">
            {thumbnails.map((thumb, index) => (
              <button
                key={`${thumb.slice(0, 40)}-${index}`}
                type="button"
                onClick={() => setActiveIndex(index)}
                className={`flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] transition ${
                  index === activeIndex
                    ? 'border-purple-300 bg-purple-50 text-purple-700'
                    : 'border-slate-200 bg-white text-slate-500 hover:bg-slate-50'
                }`}
              >
                <ImageIcon className="size-3" />
                {labels[index] || `图 ${index + 1}`}
              </button>
            ))}
          </div>
        ) : null}
        {data.materials.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {data.materials.map((m) => <TagPill key={m}>{m}</TagPill>)}
          </div>
        ) : null}
        {data.estimatedPrice ? (
          <div className="flex items-center gap-1.5 text-xs text-slate-600">
            <Wallet className="size-3.5 text-slate-400" />
            预估造价 ¥{data.estimatedPrice.min.toLocaleString()} - ¥{data.estimatedPrice.max.toLocaleString()}
          </div>
        ) : null}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <CardAction onClick={onPreview} primary>查看方案预览</CardAction>
        <CardAction onClick={() => onPromote('design_sheet')}>设计图</CardAction>
        <CardAction onClick={() => onPromote('plan_2d')}>2D平面图</CardAction>
        <CardAction onClick={() => onPromote('render')}>宣传图</CardAction>
        <CardAction onClick={() => onPromote('scene_fusion')}>场景融合图</CardAction>
        <CardAction onClick={() => onPromote('explosion')}>爆炸图</CardAction>
        <CardAction onClick={() => onPromote('3d')}>仿3D</CardAction>
        <CardAction onClick={() => onPromote('cad')}>CAD图纸</CardAction>
        <CardAction onClick={() => onPromote('quote')}>报价</CardAction>
        <CardAction onClick={() => onPromote('package')}>工程包</CardAction>
      </div>
    </CardShell>
  );
};

/* ── 4.4 报价卡片 ── */

export const QuoteCard: React.FC<{
  data: QuoteCardData;
  onViewDetail: () => void;
  onRecalculate: () => void;
}> = ({ data, onViewDetail, onRecalculate }) => (
  <CardShell icon={<Calculator className="size-3.5" />} accent="emerald" title={`报价单 ${data.quoteId}`}>
    <div className="space-y-1.5 text-xs">
      <div className="flex items-center justify-between">
        <span className="text-slate-400">方案</span>
        <span className="text-slate-800">{data.schemeName}</span>
      </div>
      <div className="flex items-center justify-between">
        <span className="text-slate-400">材料成本</span>
        <span className="text-slate-800">¥{data.materialCost.toLocaleString()}</span>
      </div>
      <div className="flex items-center justify-between">
        <span className="text-slate-400">生产成本</span>
        <span className="text-slate-800">¥{data.productionCost.toLocaleString()}</span>
      </div>
      <div className="flex items-center justify-between border-t border-slate-100 pt-1.5">
        <span className="font-medium text-slate-600">客户报价</span>
        <span className="text-base font-semibold text-slate-900">¥{data.totalCustomer.toLocaleString()}</span>
      </div>
    </div>
    <div className="mt-2 flex items-center gap-1.5">
      <CardAction onClick={onViewDetail} primary>查看详情</CardAction>
      <CardAction onClick={onRecalculate}>重新计算</CardAction>
    </div>
  </CardShell>
);

/* ── 4.5 状态卡片（执行中） ── */

export const StatusCard: React.FC<{ data: StatusCardData }> = ({ data }) => {
  const pct = Math.max(0, Math.min(100, data.progress));
  return (
    <CardShell icon={<Loader2 className="size-3.5 animate-spin" />} accent="amber" title={data.task || '任务执行中'}>
      <div className="space-y-2">
        <div className="flex items-center justify-between text-xs">
          <span className="flex items-center gap-1.5 text-slate-600">
            <Wand2 className="size-3.5 text-amber-500" />
            {data.stage}
          </span>
          <span className="font-medium text-slate-800">{pct}%</span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
          <div className="h-full rounded-full bg-gradient-to-r from-amber-400 to-orange-500 transition-all duration-500" style={{ width: `${pct}%` }} />
        </div>
        {data.estimatedRemaining ? (
          <div className="text-[11px] text-slate-400">预计剩余 {data.estimatedRemaining}</div>
        ) : null}
      </div>
    </CardShell>
  );
};

/* ── 4.6 下一步推荐卡片 ── */

export const MaterialsRequestCard: React.FC<{
  data: MaterialsRequestCardData;
  onAction?: (action: string, data: Record<string, unknown>) => void;
}> = ({ data, onAction }) => {
  const fileRef = useRef<HTMLInputElement>(null);
  const [values, setValues] = React.useState<Record<string, string>>(() => {
    const initial: Record<string, string> = {};
    for (const field of data.fields) {
      if (field.collected && data.collected?.[field.key]) {
        initial[field.key] = data.collected[field.key];
      }
    }
    return initial;
  });
  const referenceImage = data.collected?.referenceImage;
  const required = Boolean(data.required);
  const canSubmit = !required || Boolean(referenceImage);

  const filledCount = Object.values(values).filter((v) => v && v.trim()).length;
  const totalCount = data.fields.length;

  const setValue = (key: string, value: string) => {
    setValues((prev) => ({ ...prev, [key]: value }));
  };

  const submit = () => {
    const trimmed: Record<string, string> = {};
    for (const [key, value] of Object.entries(values)) {
      if (value && value.trim()) trimmed[key] = value.trim();
    }
    onAction?.('materials.submit', { values: trimmed });
  };

  return (
    <CardShell icon={<ClipboardCheck className="size-3.5" />} accent="amber" title={required ? '上传参考图（必填）' : '补充设计材料（选填）'}>
      <input
        ref={fileRef}
        type="file"
        accept="image/*,.pdf,.step,.stp,.stl,.glb,.dxf"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (file) onAction?.('materials.upload', { file });
        }}
      />
      <div className="mb-2 text-xs text-slate-500">
        {data.description || (required
          ? `项目「${data.projectName}」需要先上传参考图，图上图任务不能跳过参考图。`
          : `项目「${data.projectName}」已创建，可在下方补充材料（选填），留空将按默认生成：`)}
      </div>
      {referenceImage ? (
        <div className="mb-2 flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50/60 px-2.5 py-2">
          <span className="flex size-10 shrink-0 items-center justify-center overflow-hidden rounded-md bg-white">
            <PreviewImage src={referenceImage} alt="参考图" className="h-full w-full object-cover" />
          </span>
          <span className="min-w-0 flex-1 text-xs text-emerald-700">已上传参考图</span>
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="rounded-lg border border-emerald-300 bg-white px-2 py-1 text-[11px] font-medium text-emerald-700 transition hover:bg-emerald-50"
          >
            更换
          </button>
        </div>
      ) : null}
      <div className="space-y-1.5">
        {data.fields.map((field) => {
          const hasValue = Boolean(values[field.key] && values[field.key].trim());
          return (
            <div
              key={field.key}
              className={`rounded-lg border px-2.5 py-2 text-xs transition ${
                hasValue ? 'border-emerald-200 bg-emerald-50/40' : 'border-slate-200 bg-white'
              }`}
            >
              <div className="mb-1 flex items-center justify-between">
                <span className={`flex items-center gap-1.5 font-medium ${hasValue ? 'text-emerald-700' : 'text-slate-700'}`}>
                  {hasValue ? <Check className="size-3 text-emerald-500" /> : null}
                  {field.label}
                </span>
                {hasValue ? (
                  <button
                    type="button"
                    onClick={() => setValue(field.key, '')}
                    className="text-[11px] text-slate-400 hover:text-rose-500"
                  >
                    清除
                  </button>
                ) : null}
              </div>
              <input
                type="text"
                value={values[field.key] ?? ''}
                onChange={(e) => setValue(field.key, e.target.value)}
                placeholder={field.hint}
                className="w-full rounded-md border border-slate-200 bg-white px-2 py-1.5 text-xs text-slate-800 outline-none placeholder:text-slate-300 focus:border-purple-300 focus:ring-2 focus:ring-purple-100"
              />
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex items-center justify-between">
        <span className="text-[11px] text-slate-400">
          已填写 {filledCount}/{totalCount} 项{required ? '（必填）' : '（选填）'}
        </span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <CardAction onClick={() => fileRef.current?.click()} primary={!referenceImage}>
          <ImageIcon className="size-3" />
          {referenceImage ? '更换参考图' : '上传参考图'}
        </CardAction>
        <CardAction onClick={submit} primary disabled={!canSubmit}>
          确认并开始生成
        </CardAction>
        {required ? null : (
          <CardAction onClick={() => onAction?.('materials.skip', {})}>
            直接生成（跳过补充）
          </CardAction>
        )}
      </div>
    </CardShell>
  );
};

/* ── 提示词确认卡片 ── */

export const PromptCard: React.FC<{
  data: PromptCardData;
  onConfirm: () => void;
  onEdit: (prompt: string) => void;
}> = ({ data, onConfirm, onEdit }) => {
  const [editing, setEditing] = React.useState(false);
  const [edited, setEdited] = React.useState(data.optimized);
  const hasRefs = data.references && data.references.length > 0;

  const handleConfirm = () => {
    if (editing) onEdit(edited);
    else onConfirm();
  };

  return (
    <CardShell icon={<Sparkles className="size-3.5" />} accent="purple" title="生成提示词">
      <div className="space-y-2.5">
        {editing ? (
          <textarea
            value={edited}
            onChange={(e) => setEdited(e.target.value)}
            rows={6}
            className="w-full rounded-lg border border-purple-200 bg-purple-50/30 px-3 py-2 text-xs text-slate-800 outline-none focus:border-purple-400 focus:ring-2 focus:ring-purple-100"
          />
        ) : (
          <div className="rounded-lg border border-purple-100 bg-purple-50/20 px-3 py-2.5 text-xs leading-relaxed text-slate-700 font-mono whitespace-pre-wrap">
            {data.optimized}
          </div>
        )}

        {data.original !== data.optimized ? (
          <details className="text-[11px]">
            <summary className="cursor-pointer text-slate-400 transition hover:text-slate-600">
              查看原始提示词
            </summary>
            <div className="mt-1 rounded-lg border border-slate-100 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-500 whitespace-pre-wrap font-mono">
              {data.original}
            </div>
          </details>
        ) : null}

        {hasRefs ? (
          <div className="flex flex-wrap gap-1 text-[10px]">
            <span className="text-slate-400">知识库参考：</span>
            {data.references.slice(0, 4).map((ref, i) => (
              <span key={i} className="rounded-full border border-purple-100 bg-purple-50 px-1.5 py-0.5 text-purple-600" title={ref.prompt}>
                {ref.source}
              </span>
            ))}
          </div>
        ) : null}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <CardAction onClick={handleConfirm} primary>
          <Sparkles className="size-3" />
          {editing ? '确认修改并生成' : '确认并生成'}
        </CardAction>
        {!editing ? (
          <CardAction onClick={() => setEditing(true)}>
            <PencilLine className="size-3" />
            修改
          </CardAction>
        ) : (
          <CardAction onClick={() => { setEditing(false); setEdited(data.optimized); }}>
            取消修改
          </CardAction>
        )}
      </div>
    </CardShell>
  );
};

export const NextStepCard: React.FC<{
  data: NextStepCardData;
  onAction: (action: NextStepCardData['recommendations'][number]) => void;
}> = ({ data, onAction }) => {
  const icons: Record<string, React.ReactNode> = {
    quote: <Calculator className="size-3.5" />,
    render: <ImageIcon className="size-3.5" />,
    scene_fusion: <Layers className="size-3.5" />,
    explosion: <Boxes className="size-3.5" />,
    '3d': <Box className="size-3.5" />,
    cad: <FileText className="size-3.5" />,
    package: <Package className="size-3.5" />,
  };
  return (
    <CardShell icon={<Sparkles className="size-3.5" />} accent="slate" title="接下来你可以">
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
        {data.recommendations.map((rec) => (
          <button
            key={rec.action}
            type="button"
            onClick={() => onAction(rec)}
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-2 text-xs font-medium text-slate-700 transition hover:border-slate-300 hover:bg-slate-50"
          >
            {icons[rec.action]}
            {rec.label}
          </button>
        ))}
      </div>
    </CardShell>
  );
};

/* ── 统一卡片分发 ── */

export const WorkflowCardView: React.FC<{
  card: WorkflowCard;
  onAction?: (action: string, data: Record<string, unknown>) => void;
  confirmed?: boolean;
}> = ({ card, onAction, confirmed }) => {
  const fire = (action: string, extra: Record<string, unknown> = {}) => {
    onAction?.(action, { cardId: card.id, ...extra });
  };

  if (confirmed && (card.type === 'project_created' || card.type === 'requirement')) {
    return (
      <CardShell
        icon={card.type === 'project_created' ? <FolderKanban className="size-3.5" /> : <ClipboardCheck className="size-3.5" />}
        accent={card.type === 'project_created' ? 'purple' : 'sky'}
        title={card.type === 'project_created' ? '项目已创建' : '结构化需求'}
      >
        <div className="flex items-center gap-1.5 text-xs text-emerald-600">
          <Check className="size-3.5" />
          已确认，方案生成中
        </div>
      </CardShell>
    );
  }

  switch (card.type) {
    case 'project_created': {
      const data = card.data as CardDataByType['project_created'];
      return (
        <ProjectCreatedCard
          data={data}
          onConfirm={(name, desc) => {
            if (name || desc) fire('project.save', { name: name || data.name, description: desc || data.description });
            else fire('project.confirm', { projectId: data.projectId });
          }}
          onEdit={() => fire('project.edit')}
        />
      );
    }
    case 'requirement': {
      const data = card.data as CardDataByType['requirement'];
      return (
        <RequirementCard
          data={data}
          onConfirm={() => fire('requirement.confirm')}
          onEdit={() => fire('requirement.edit')}
        />
      );
    }
    case 'design_scheme': {
      const data = card.data as CardDataByType['design_scheme'];
      return (
        <DesignSchemeCard
          data={data}
          onPreview={() => fire('scheme.preview')}
          onPromote={(action) => fire('scheme.promote', { promote: action })}
        />
      );
    }
    case 'quote': {
      const data = card.data as CardDataByType['quote'];
      return (
        <QuoteCard
          data={data}
          onViewDetail={() => fire('quote.view')}
          onRecalculate={() => fire('quote.recalculate')}
        />
      );
    }
    case 'status': {
      const data = card.data as CardDataByType['status'];
      return <StatusCard data={data} />;
    }
    case 'prompt_confirm': {
      const data = card.data as CardDataByType['prompt_confirm'];
      return (
        <PromptCard
          data={data}
          onConfirm={() => fire('prompt.confirm', {})}
          onEdit={(prompt: string) => fire('prompt.edit', { prompt })}
        />
      );
    }
    case 'next_step': {
      const data = card.data as CardDataByType['next_step'];
      return (
        <NextStepCard
          data={data}
          onAction={(rec) => fire('next.action', { nextAction: rec.action })}
        />
      );
    }
    case 'materials_request': {
      const data = card.data as CardDataByType['materials_request'];
      return (
        <MaterialsRequestCard
          data={data}
          onAction={(action, payload) => fire(action, payload)}
        />
      );
    }
    default:
      return null;
  }
};

export { ArrowRight, Ruler, Layers, PencilLine, Check };
