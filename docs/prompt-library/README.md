# 提示词花园完整库

从 [提示词花园](https://garden.always200.com) 整理的完整AI绘画提示词库，包含87个精选提示词。

## 库结构

```
docs/prompt-library/
├── README.md              # 本文档
├── prompts.json           # 完整提示词数据（87个）
├── categories.json        # 分类索引（20个分类）
├── tags.json              # 标签统计（301个标签）
└── batch*.json            # 各批次原始数据
```

## 快速开始

### 1. 浏览提示词

```python
import json

with open('prompts.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# 显示所有提示词标题
for p in data['prompts']:
    print(f"[{p['id']}] {p['title']}")
```

### 2. 按分类查找

```python
# 获取信息表达类提示词
info_prompts = [p for p in data['prompts'] if p['category'] == '信息表达']

# 获取审美风格类提示词
style_prompts = [p for p in data['prompts'] if p['category'] == '审美风格']
```

### 3. 按标签查找

```python
# 查找所有流程图相关提示词
flow_prompts = [p for p in data['prompts'] if '流程图' in p.get('tags', [])]

# 查找所有海报相关提示词
poster_prompts = [p for p in data['prompts'] if '海报' in str(p.get('tags', []))]
```

### 4. 使用提示词模板

```python
def use_prompt(prompt_id, **params):
    """使用提示词并填充参数"""
    prompt = next(p for p in data['prompts'] if p['id'] == prompt_id)
    text = prompt['prompt']
    for key, value in params.items():
        text = text.replace(f'{{{{{key}}}}}', value)
    return text

# 示例：使用流程信息图提示词
result = use_prompt(
    'NB-004',
    source_material='新员工入职流程：HR确认日期，行政准备工位...'
)
```

### 5. 使用导入码

每个提示词都有导入码（如 `ZH-NB-004`），可在 [提示词优化器](https://prompt.always200.com) 中直接导入。

## 分类体系

| 分类 | 数量 | 说明 |
|------|------|------|
| 信息表达 | 15 | 流程图、信息图、结构拆解等 |
| 审美风格 | 18 | 插画风格、摄影氛围等 |
| 角色与叙事 | 10 | 角色海报、故事插画等 |
| 宣传海报 | 10 | 电影海报、文旅海报等 |
| 社交娱乐 | 10 | 社交分享、头像等 |
| 商品与品牌 | 7 | 产品展示、品牌视觉等 |
| 3D与空间 | 7 | 微缩场景、空间沙盘等 |
| 工作工具 | 6 | 分镜设定、角色设定等 |

## 热门标签

| 标签 | 数量 | 标签 | 数量 |
|------|------|------|------|
| 文生图 | 87 | 信息图 | 8 |
| 海报 | 12 | 插画 | 10 |
| 角色 | 8 | 人像 | 7 |
| 概念封面 | 5 | 水彩 | 5 |
| 电影海报 | 4 | 微缩场景 | 4 |

## 数据格式

### 提示词对象结构

```json
{
  "id": "NB-004",
  "title": "根据文本生成流程信息图",
  "slug": "text-to-flow-infographic",
  "category": "信息表达",
  "subcategory": "流程图",
  "tags": ["流程图", "信息图", "文本"],
  "description": "把流程文字、说明文本或摘要整理成精美、清晰、语言自适应的流程信息图",
  "prompt": "完整的提示词内容...",
  "parameters": [
    {
      "name": "source_material",
      "label": "来源材料",
      "required": true,
      "description": "参数说明"
    }
  ],
  "importCode": "ZH-NB-004",
  "author": "花园团队",
  "license": "Community",
  "examples": [
    {
      "title": "示例标题",
      "params": {"param": "value"},
      "importCode": "ZH-NB-004@ex-001"
    }
  ],
  "promptPageUrl": "https://garden.always200.com/prompts/text-to-flow-infographic"
}
```

## 集成到知识库

本库可集成到项目的 Milvus 知识库系统：

```python
from app.services.knowledge_base_service import knowledge_base_service

# 将提示词转换为知识库格式
for prompt in prompts:
    content = f"提示词：{prompt['title']}\n\n{prompt['prompt']}"
    # 向量化并入库...
```

## 统计信息

- **总提示词数**: 87
- **分类数**: 20
- **标签数**: 301
- **数据来源**: https://garden.always200.com
- **更新日期**: 2026-08-06

## 更新日志

- **2026-08-06**: 完整版本，包含87个提示词
- **2026-08-06**: 初始版本，包含5个精选提示词

## 许可证

提示词内容来自 [提示词花园](https://garden.always200.com)，遵循 Community 许可。