import React from 'react';
import { Sparkles, UploadCloud } from 'lucide-react';
import type { ForgeCadImportAsset } from '../services/forgecadService';
import type {
  IndustryCategory,
  IndustryRoot,
  IndustryTemplate,
  PartNode,
  BomRow,
  ProjectInputMode,
  UploadDesignIntent,
  CoCreationScenario,
  ScenarioConfig,
  ScenarioTab,
} from './CoCreationAgentWorkspace.types';

export const acceptedCadImportExtensions = ['step', 'stp', 'stl', 'dxf', 'dwg', 'pdf', 'png', 'jpg', 'jpeg', 'webp'];
export const maxCadImportSizeBytes = 50 * 1024 * 1024;

export const industryCategories: IndustryCategory[] = ['装备制造', '汽车零部件', '医疗器械', '家居智造'];

export const industryCatalog: IndustryRoot[] = [
  {
    id: 'equipment',
    label: '装备制造',
    groups: [
      {
        id: 'motion',
        label: '运动控制装备',
        segments: [
          {
            id: 'servo',
            label: '伺服执行机构',
            leaves: [
              {
                id: 'servo-base',
                label: '伺服联动底座',
                keywords: ['伺服', '底座', '联动', '安装座', '运动控制'],
                prefill: {
                  projectName: '伺服联动底座结构设计',
                  description: '设计一套伺服联动底座，重点约束安装孔位、加强筋、减重槽、装配基准面和维护空间，输出工程图、3D 预览、效果图和爆炸图。',
                  fileTips: '适合上传 STEP、STL、DXF、装配草图或现有底座照片。',
                },
              },
              {
                id: 'linear-module',
                label: '直线模组滑台',
                keywords: ['直线模组', '滑台', '导轨', '丝杆', '电机座'],
                prefill: {
                  projectName: '直线模组滑台结构设计',
                  description: '设计直线模组滑台，包含导轨安装面、丝杆支撑、限位传感器位置、拖链空间和防护罩装配关系。',
                  fileTips: '适合上传导轨截面图、设备安装尺寸、STEP 或 PDF 图纸。',
                },
              },
              {
                id: 'rotary-table',
                label: '精密旋转工作台',
                keywords: ['旋转台', '转盘', '分度盘', '轴承座', '回转'],
                prefill: {
                  projectName: '精密旋转工作台设计',
                  description: '设计精密旋转工作台，关注回转支撑、定位孔、驱动接口、线缆通道和上装夹具的装配基准。',
                  fileTips: '适合上传转盘草图、安装孔位表、STEP 或实物照片。',
                },
              },
            ],
          },
          {
            id: 'cabinet',
            label: '控制与防护结构',
            leaves: [
              {
                id: 'control-cabinet',
                label: '设备控制柜',
                keywords: ['控制柜', '电控柜', '钣金', '散热', '柜体'],
                prefill: {
                  projectName: '设备控制柜结构设计',
                  description: '设计设备控制柜，包含柜体钣金、门板、通风散热、仪表开孔、线缆走线、安装背板和检修空间。',
                  fileTips: '适合上传柜体草图、钣金展开图、PDF 图纸或现场照片。',
                },
              },
              {
                id: 'protective-cover',
                label: '设备防护罩',
                keywords: ['防护罩', '护罩', '钣金罩', '透明窗', '安全门'],
                prefill: {
                  projectName: '设备防护罩结构设计',
                  description: '设计设备防护罩，重点考虑钣金折弯、透明观察窗、开门铰链、安全互锁和维护拆装。',
                  fileTips: '适合上传现场照片、设备外形尺寸、草图或 DXF。',
                },
              },
            ],
          },
        ],
      },
      {
        id: 'fixtures',
        label: '工装夹具',
        segments: [
          {
            id: 'welding',
            label: '焊接装配工装',
            leaves: [
              {
                id: 'welding-fixture',
                label: '焊接定位夹具',
                keywords: ['焊接', '夹具', '定位', '压紧', '工装'],
                prefill: {
                  projectName: '焊接定位夹具设计',
                  description: '设计焊接定位夹具，包含定位销、压紧机构、基准块、防错结构、焊枪避让和快速换型要求。',
                  fileTips: '适合上传工件 STEP、焊接工艺图、夹具草图或现场照片。',
                },
              },
              {
                id: 'inspection-fixture',
                label: '检测定位治具',
                keywords: ['检测', '治具', '检具', '定位', '测量'],
                prefill: {
                  projectName: '检测定位治具设计',
                  description: '设计检测定位治具，关注重复定位、测量避让、快速装夹、轻量化和检验基准标识。',
                  fileTips: '适合上传被测件图纸、检测点位表、STEP 或 PDF。',
                },
              },
            ],
          },
        ],
      },
    ],
  },
  {
    id: 'auto',
    label: '汽车零部件',
    groups: [
      {
        id: 'chassis',
        label: '底盘与车身件',
        segments: [
          {
            id: 'bracket',
            label: '支架连接件',
            leaves: [
              {
                id: 'mounting-bracket',
                label: '轻量化安装支架',
                keywords: ['汽车', '支架', '轻量化', '安装', '冲压'],
                prefill: {
                  projectName: '轻量化安装支架设计',
                  description: '设计汽车零部件安装支架，重点控制安装孔、翻边加强、冲压可制造性、减重孔和装配避让。',
                  fileTips: '适合上传原支架图纸、实物照片、冲压件草图或 DXF。',
                },
              },
              {
                id: 'battery-tray',
                label: '电池托盘连接件',
                keywords: ['电池托盘', '新能源', '连接件', '铝合金', '车身'],
                prefill: {
                  projectName: '电池托盘连接件设计',
                  description: '设计新能源电池托盘连接件，关注承载、密封、焊接边、螺栓连接、碰撞吸能和装配路径。',
                  fileTips: '适合上传托盘边界尺寸、安装孔位图、STEP 或 PDF。',
                },
              },
            ],
          },
        ],
      },
      {
        id: 'interior',
        label: '内外饰结构',
        segments: [
          {
            id: 'trim',
            label: '塑料结构件',
            leaves: [
              {
                id: 'interior-trim-clip',
                label: '内饰卡扣结构',
                keywords: ['内饰', '卡扣', '塑料件', '注塑', '装配'],
                prefill: {
                  projectName: '内饰卡扣结构设计',
                  description: '设计内饰卡扣结构，包含卡接弹性、拔出力、注塑拔模、加强筋、装配导向和防异响细节。',
                  fileTips: '适合上传卡扣草图、装配截面、实物照片或 STEP。',
                },
              },
            ],
          },
        ],
      },
    ],
  },
  {
    id: 'medical',
    label: '医疗器械',
    groups: [
      {
        id: 'diagnostic',
        label: '诊疗设备结构',
        segments: [
          {
            id: 'portable',
            label: '便携设备',
            leaves: [
              {
                id: 'portable-ultrasound-shell',
                label: '便携超声外壳',
                keywords: ['医疗', '超声', '外壳', '便携', '散热'],
                prefill: {
                  projectName: '便携超声外壳结构设计',
                  description: '设计便携超声设备外壳，重点考虑手持握持、散热、接口防护、消毒清洁、屏幕装配和跌落防护。',
                  fileTips: '适合上传外观草图、主板尺寸、接口位置图或实物照片。',
                },
              },
              {
                id: 'monitor-cart',
                label: '监护设备推车',
                keywords: ['监护', '推车', '医疗车', '支架', '移动'],
                prefill: {
                  projectName: '监护设备推车设计',
                  description: '设计监护设备推车，包含立柱、托盘、线缆收纳、脚轮、重心稳定和设备快拆接口。',
                  fileTips: '适合上传设备尺寸、推车草图、参考照片或 STEP。',
                },
              },
            ],
          },
          {
            id: 'sterile',
            label: '耗材与无菌结构',
            leaves: [
              {
                id: 'reagent-cartridge',
                label: '试剂盒卡匣',
                keywords: ['试剂盒', '卡匣', '耗材', '流道', '注塑'],
                prefill: {
                  projectName: '试剂盒卡匣结构设计',
                  description: '设计试剂盒卡匣，关注流道布局、密封筋、定位扣位、防呆结构、注塑拔模和批量装配。',
                  fileTips: '适合上传流道草图、样件照片、注塑件图纸或 PDF。',
                },
              },
            ],
          },
        ],
      },
    ],
  },
  {
    id: 'home',
    label: '家居智造',
    groups: [
      {
        id: 'custom-furniture',
        label: '定制家具',
        segments: [
          {
            id: 'cabinet-storage',
            label: '柜体收纳',
            leaves: [
              {
                id: 'child-room-cabinet',
                label: '儿童房全屋定制',
                keywords: ['儿童房', '衣柜', '书桌', '榻榻米', '定制'],
                prefill: {
                  projectName: '儿童房全屋定制设计',
                  description: '设计儿童房全屋定制方案，包含衣柜、书桌、开放层板、窗帘盒、榻榻米收纳和安全圆角要求。',
                  fileTips: '适合上传户型图、现场照片、手绘草图或 PDF 图纸。',
                },
              },
              {
                id: 'entry-cabinet',
                label: '玄关收纳柜',
                keywords: ['玄关', '鞋柜', '收纳', '开放格', '换鞋凳'],
                prefill: {
                  projectName: '玄关收纳柜设计',
                  description: '设计玄关收纳柜，包含鞋柜、换鞋凳、挂衣区、开放格、灯带预留和柜体尺寸约束。',
                  fileTips: '适合上传户型尺寸、现场照片、草图或 PDF。',
                },
              },
            ],
          },
          {
            id: 'smart-home',
            label: '智能家居结构',
            leaves: [
              {
                id: 'smart-curtain-box',
                label: '智能窗帘盒',
                keywords: ['智能窗帘', '窗帘盒', '电机', '轨道', '家居'],
                prefill: {
                  projectName: '智能窗帘盒结构设计',
                  description: '设计智能窗帘盒，关注电机安装、轨道检修、遮光挡板、走线空间和现场安装尺寸。',
                  fileTips: '适合上传窗口尺寸、轨道照片、草图或 DXF。',
                },
              },
            ],
          },
        ],
      },
    ],
  },
];

export const templates: IndustryTemplate[] = [];

export const partTree: PartNode[] = [];

export const bomRows: BomRow[] = [];

export const inputModes: Array<{ id: ProjectInputMode; label: string; icon: React.ElementType }> = [
  { id: 'prompt', label: '文字生成', icon: Sparkles },
  { id: 'upload', label: '上传图纸', icon: UploadCloud },
];

export const uploadDesignIntents: Array<{ id: UploadDesignIntent; label: string; description: string }> = [
  { id: 'drawing', label: '图纸识别', description: '上传工程图、CAD、PDF 或草图继续生成设计方案' },
  { id: 'objectToDrawing', label: '实物转图纸', description: '上传实物照片，提取轮廓、尺寸线索并生成工程图' },
];

export const initialVersionSnapshots: never[] = [];

export const emptyStepPreviewAsset: ForgeCadImportAsset = {
  assetId: 'step-preview-placeholder',
  filename: 'STEP 预览',
  extension: 'step',
  contentType: 'model/step',
  sizeBytes: 0,
  storagePath: '',
  createdAt: new Date().toISOString(),
  parseStatus: 'pending',
  parseMessage: '等待 STEP 转换器',
  parseFeatures: [],
  previewKind: 'step_pending_conversion',
  previewEntities: [],
  bomItems: [],
  explosionSteps: [],
  previewAssetUrl: null,
  conversionStatus: 'pending',
  conversionMessage: '等待 STEP 转换器',
  previewAssetPath: null,
  downloadUrl: '',
};

export const workspacePreviewHeightClass = 'h-[clamp(280px,calc(100vh-19rem),720px)]';
export const workspacePreviewImageFrameClass = 'flex h-[calc(100%-2.25rem)] min-h-0 w-full items-center justify-center overflow-hidden rounded-xl bg-slate-50 p-4';
export const workspacePreviewImageClass = 'h-auto max-h-full w-auto max-w-full rounded-lg object-contain shadow-sm';

export const scenarioConfigs: Record<CoCreationScenario, ScenarioConfig> = {
  design: {
    label: '设计',
    description: '参考图 / 文字 -> 2D 平面图 -> 设计图',
    steps: ['参考图/文字', '2D 平面图', '设计图'],
    toneClass: 'from-cyan-500 to-blue-600',
  },
  propaganda: {
    label: '宣发',
    description: '精修图 -> 场景融合图',
    steps: ['精修图', '场景融合图'],
    toneClass: 'from-violet-500 to-fuchsia-600',
  },
  production: {
    label: '生产',
    description: '基于设计图生成 3D 打样 -> STEP 图',
    steps: ['3D 打样', 'STEP 图'],
    toneClass: 'from-emerald-500 to-teal-600',
  },
};

export const scenarioTabs: ScenarioTab[] = [
  { id: 'design', label: '设计', description: '参考图、文字到 2D 平面图和设计图' },
  { id: 'propaganda', label: '宣发', description: '精修图到产品融合场景图' },
];
