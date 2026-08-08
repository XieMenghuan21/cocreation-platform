import React, { useState } from 'react';
import {
  Loader2,
  Send,
  Paperclip,
  Mic,
  Camera,
  Palette,
  Megaphone,
  Factory,
  Sparkles,
} from 'lucide-react';

interface LandingPageProps {
  userLabel: string | null;
  isAuthenticating: boolean;
  isIframe: boolean;
  onEnter: () => void;
  onLogin: () => void;
  onSubmitPrompt: (prompt: string) => void;
}

const ROUTES = [
  {
    icon: Palette,
    title: '设计',
    subtitle: '从需求到方案',
    desc: 'AI 深度理解需求，自动拆解任务，驱动多智能体协作完成从概念到效果的全流程。',
    tags: ['需求分析', '概念设计', '效果图', '方案对比'],
    iconBg: 'bg-purple-100',
    iconText: 'text-purple-600',
    badgeClass: 'border-purple-200 text-purple-700 bg-purple-50',
  },
  {
    icon: Megaphone,
    title: '宣发',
    subtitle: '从方案到素材',
    desc: '基于设计方案自动生成电商主图、场景融合图、3D 爆炸图和视频脚本。',
    tags: ['场景融合', '电商主图', '3D爆炸图', '视频脚本'],
    iconBg: 'bg-amber-100',
    iconText: 'text-amber-600',
    badgeClass: 'border-amber-200 text-amber-700 bg-amber-50',
  },
  {
    icon: Factory,
    title: '生产',
    subtitle: '从方案到生产',
    desc: '输出可直接用于生产的 CAD 模型、BOM 清单、工艺路线和完整生产包。',
    tags: ['CAD建模', 'BOM清单', '工艺路线', '生产包'],
    iconBg: 'bg-green-100',
    iconText: 'text-green-600',
    badgeClass: 'border-green-200 text-green-700 bg-green-50',
  },
];

export const LandingPage: React.FC<LandingPageProps> = ({
  userLabel,
  isAuthenticating,
  isIframe,
  onEnter,
  onLogin,
  onSubmitPrompt,
}) => {
  const [text, setText] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = (value?: string) => {
    const input = (value || text).trim();
    if (!input || loading) return;
    setLoading(true);
    onSubmitPrompt(input);
  };

  const enterLabel = userLabel ? '进入工作台' : '开始使用';

  return (
    <div className="flex h-full flex-col bg-white">
      <div className="fixed right-6 top-6 z-10 flex items-center gap-3">
        {userLabel ? (
          <span className="text-xs text-slate-500">{userLabel}</span>
        ) : null}
        {userLabel ? (
          <button
            type="button"
            onClick={onEnter}
            className="rounded-lg border border-slate-200 bg-white px-3.5 py-1.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50"
          >
            进入工作台
          </button>
        ) : null}
        {isIframe ? (
          <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">
            SSO
          </span>
        ) : null}
      </div>

      <main className="flex flex-1 flex-col items-center justify-center overflow-y-auto px-4">
        <div className="w-full max-w-[820px] space-y-10 py-16">
          <div className="text-center">
            <span className="text-[11px] uppercase tracking-[1.5px] text-slate-400">
              AI DESIGN STUDIO
            </span>
          </div>

          <div className="space-y-3 text-center">
            <h1 className="text-[40px] font-light leading-tight tracking-tight text-slate-900 sm:text-[48px]">
              今天你想设计什么?
            </h1>
            <p className="text-sm text-slate-500">
              对话即工作流 — 说出需求，AI 自动推进，直到交付
            </p>
          </div>

          <div>
            <div className="relative rounded-[10px] border border-slate-200 bg-white px-4 py-3 shadow-sm transition-all focus-within:border-slate-900 focus-within:ring-[3px] focus-within:ring-slate-900/10">
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    handleSubmit();
                  }
                }}
                placeholder="描述你的设计需求…"
                rows={1}
                className="w-full resize-none border-0 bg-transparent pr-12 text-sm text-slate-900 outline-none placeholder:text-slate-400"
              />
              <button
                type="button"
                onClick={() => handleSubmit()}
                disabled={!text.trim() || loading}
                className="absolute right-3 top-1/2 flex size-8 -translate-y-1/2 items-center justify-center rounded-full bg-slate-900 text-white shadow-sm transition hover:bg-slate-700 disabled:opacity-40"
              >
                {loading ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Send className="size-4" />
                )}
              </button>
            </div>

            <div className="mt-3 flex items-center justify-center gap-2">
              {[
                { icon: Paperclip, label: '上传文件', coming: true },
                { icon: Mic, label: '语音', coming: true },
                { icon: Camera, label: '拍照', coming: true },
              ].map((pill) => {
                const Icon = pill.icon;
                return (
                  <button
                    key={pill.label}
                    type="button"
                    disabled
                    title="开发中"
                    className="flex cursor-not-allowed items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3.5 py-1.5 text-xs text-slate-400 transition"
                  >
                    <Icon className="size-3.5" />
                    {pill.label}
                    <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-400">
                      开发中
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {ROUTES.map((route) => {
              const Icon = route.icon;
              return (
                <button
                  key={route.title}
                  type="button"
                  onClick={() => handleSubmit(route.desc)}
                  className="group text-left"
                >
                  <div className="h-full rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition-all group-hover:border-slate-300 group-hover:shadow-md">
                    <div className={`mb-3 inline-flex size-9 items-center justify-center rounded-lg ${route.iconBg}`}>
                      <Icon className={`size-[18px] ${route.iconText}`} />
                    </div>
                    <h3 className="mb-0.5 text-sm font-semibold text-slate-900">
                      {route.title}
                    </h3>
                    <p className="mb-3 text-xs text-slate-500">
                      {route.subtitle}
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {route.tags.map((tag) => (
                        <span
                          key={tag}
                          className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${route.badgeClass}`}
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>

          {isAuthenticating ? (
            <div className="flex justify-center">
              <span className="flex items-center gap-2 rounded-xl bg-slate-900 px-5 py-3 text-sm font-medium text-white">
                <Loader2 className="size-4 animate-spin" />
                正在验证登录…
              </span>
            </div>
          ) : !userLabel && onLogin ? (
            <div className="flex justify-center">
              <button
                type="button"
                onClick={onLogin}
                className="flex items-center gap-2 rounded-xl bg-slate-900 px-6 py-3 text-sm font-medium text-white transition hover:bg-slate-700"
              >
                登录后开始
              </button>
            </div>
          ) : null}
        </div>
      </main>

      <footer className="py-6 text-center text-xs text-slate-400">
        © 2026 CoDesign AI
      </footer>
    </div>
  );
};