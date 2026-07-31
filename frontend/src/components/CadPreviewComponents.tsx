import { useState } from 'react';
import type { ForgeCadImportAsset, ForgeCadExplosionStep } from '../services/forgecadService';
import { workspacePreviewHeightClass } from './CoCreationAgentWorkspace.constants';

const DxfPreview: React.FC<{ asset: ForgeCadImportAsset }> = ({ asset }) => {
  const points = asset.previewEntities.flatMap((entity) => [
    ...(entity.points || []),
    ...(entity.center ? [
      entity.center,
      [entity.center[0] - (entity.radius || 0), entity.center[1] - (entity.radius || 0)],
      [entity.center[0] + (entity.radius || 0), entity.center[1] + (entity.radius || 0)],
    ] : []),
  ]);
  const xs = points.map((point) => point[0]);
  const ys = points.map((point) => point[1]);
  const minX = xs.length ? Math.min(...xs) - 20 : -100;
  const maxX = xs.length ? Math.max(...xs) + 20 : 100;
  const minY = ys.length ? Math.min(...ys) - 20 : -100;
  const maxY = ys.length ? Math.max(...ys) + 20 : 100;
  const viewBox = `${minX} ${minY} ${Math.max(maxX - minX, 1)} ${Math.max(maxY - minY, 1)}`;

  return (
    <div className="w-full rounded-2xl border border-white/10 bg-slate-950/80 p-4">
      <svg viewBox={viewBox} className={`${workspacePreviewHeightClass} w-full rounded-xl bg-slate-950`}>
        <g transform={`scale(1,-1) translate(0,${-(minY + maxY)})`}>
          {asset.previewEntities.map((entity, index) => {
            if (entity.entityType === 'LINE' && entity.points.length >= 2) {
              return <line key={index} x1={entity.points[0][0]} y1={entity.points[0][1]} x2={entity.points[1][0]} y2={entity.points[1][1]} stroke="#67e8f9" strokeWidth="1.5" />;
            }
            if (entity.entityType === 'CIRCLE' && entity.center && entity.radius) {
              return <circle key={index} cx={entity.center[0]} cy={entity.center[1]} r={entity.radius} fill="none" stroke="#a7f3d0" strokeWidth="1.5" />;
            }
            if ((entity.entityType === 'LWPOLYLINE' || entity.entityType === 'ARC') && entity.points.length >= 2) {
              return <polyline key={index} points={entity.points.map((point) => point.join(',')).join(' ')} fill="none" stroke="#fcd34d" strokeWidth="1.5" />;
            }
            return null;
          })}
        </g>
      </svg>
      <div className="mt-3 text-xs text-slate-400">DXF 当前支持 LINE / CIRCLE / ARC / LWPOLYLINE 基础图元在线预览。</div>
    </div>
  );
};

const StepProxyPreview: React.FC<{ asset: ForgeCadImportAsset }> = ({ asset }) => {
  const items = asset.bomItems.length > 0
    ? asset.bomItems
    : [{ name: asset.filename, quantity: 1, material: null, size: null, source: 'import' }];

  return (
    <div className={`relative ${workspacePreviewHeightClass} w-full overflow-hidden rounded-2xl border border-white/10 bg-slate-950/80`}>
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_35%_20%,rgba(20,184,166,0.24),transparent_32%),radial-gradient(circle_at_70%_68%,rgba(99,102,241,0.2),transparent_30%)]" />
      <div className="absolute left-5 top-5 z-10">
        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300">STEP Proxy Preview</div>
        <div className="mt-2 max-w-xl text-sm leading-6 text-slate-300">
          已读取 STEP 基础实体和产品名；这里用装配占位块展示层级，真实曲面预览需接 STEP 转换器。
        </div>
        {asset.conversionMessage ? (
          <div className="mt-2 max-w-xl rounded-xl border border-amber-300/20 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-100">
            {asset.conversionMessage}
          </div>
        ) : null}
      </div>
      <div className="absolute inset-0 flex items-center justify-center pt-16">
        {items.slice(0, 8).map((item, index) => (
          <div
            key={`${item.name}-${index}`}
            className="absolute flex h-20 w-32 items-center justify-center rounded-2xl border border-cyan-200/35 bg-cyan-400/15 px-3 text-center text-xs font-semibold text-white shadow-[0_18px_45px_rgba(34,211,238,0.18)]"
            style={{
              transform: `translate3d(${(index - (items.length - 1) / 2) * 42}px, ${Math.sin(index) * 24}px, 0) rotateX(58deg) rotateZ(-28deg)`,
            }}
          >
            {item.name}
          </div>
        ))}
      </div>
      <div className="absolute bottom-4 left-4 right-4 grid gap-2 sm:grid-cols-3">
        {asset.parseFeatures.slice(0, 3).map((feature) => (
          <div key={`${feature.label}-${feature.value}`} className="rounded-xl bg-white/8 p-3 text-xs text-slate-200">
            <span className="text-slate-400">{feature.label}：</span>{feature.value}
          </div>
        ))}
      </div>
    </div>
  );
};

const ExplodedPreview: React.FC<{ steps: ForgeCadExplosionStep[] }> = ({ steps }) => {
  const [activeStep, setActiveStep] = useState(0);
  const visibleSteps = steps.length > 0
    ? steps
    : [{ step: 1, name: '整体模型', offset: [0, 0, 0], description: '暂无可拆解层级。' }];
  const boundedActiveStep = Math.min(activeStep, visibleSteps.length - 1);

  return (
    <div className={`relative ${workspacePreviewHeightClass} w-full overflow-hidden rounded-2xl border border-white/10 bg-slate-950/80`}>
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(56,189,248,0.18),transparent_48%)]" />
      <div className="absolute inset-0 flex items-center justify-center">
        {visibleSteps.map((step, index) => {
          const x = step.offset[0] || index * 24;
          const y = step.offset[1] || index * 12;
          const z = step.offset[2] || index * 8;
          const isActive = index === boundedActiveStep;
          return (
            <div
              key={`${step.step}-${step.name}`}
              className={`absolute rounded-2xl border px-6 py-4 text-center text-white shadow-[0_20px_60px_rgba(34,211,238,0.2)] transition-all duration-500 ${
                isActive ? 'border-amber-200 bg-amber-300/30' : 'border-cyan-200/40 bg-cyan-400/20'
              }`}
              style={{
                transform: `translate3d(${x * (boundedActiveStep + 1) / visibleSteps.length}px, ${-y * (boundedActiveStep + 1) / visibleSteps.length}px, 0) scale(${isActive ? 1.12 : 1 + z / 160})`,
              }}
            >
              <div className="text-xs text-cyan-100">STEP {step.step}</div>
              <div className="mt-1 text-sm font-bold">{step.name}</div>
            </div>
          );
        })}
      </div>
      <div className="absolute right-4 top-4 flex flex-wrap gap-2">
        {visibleSteps.map((step, index) => (
          <button
            key={`step-button-${step.step}`}
            type="button"
            onClick={() => setActiveStep(index)}
            className={`rounded-full px-3 py-1 text-xs font-semibold transition ${
              index === boundedActiveStep ? 'bg-amber-300 text-slate-950' : 'bg-white/10 text-slate-200 hover:bg-white/15'
            }`}
          >
            {step.step}
          </button>
        ))}
      </div>
      <div className="absolute bottom-4 left-4 right-4 grid gap-2 sm:grid-cols-2">
        {[visibleSteps[boundedActiveStep], ...visibleSteps.filter((_, index) => index !== boundedActiveStep).slice(0, 3)].map((step) => (
          <div key={`desc-${step.step}`} className="rounded-xl bg-white/8 p-3 text-xs leading-5 text-slate-200">
            {step.step}. {step.description}
          </div>
        ))}
      </div>
    </div>
  );
};

export { DxfPreview, StepProxyPreview, ExplodedPreview };
