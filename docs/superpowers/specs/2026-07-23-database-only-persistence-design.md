# 共创平台全量数据库持久化设计

日期：2026-07-23

## 1. 目标

本次改造将共创平台的持久化来源统一为 PostgreSQL，消除浏览器存储、进程内任务字典和磁盘业务文件形成的多套事实来源。

改造完成后必须满足：

1. 项目、版本、工作区状态、参考资产、资产库和工作流任务全部写入 PostgreSQL。
2. 图片、CAD、PDF、SVG、GLB、STEP、STL、脚本及上传原文件的文件本体也写入 PostgreSQL。
3. 前端不使用 `localStorage`、`sessionStorage` 或 IndexedDB 保存认证信息或业务数据。
4. 后端重启后，用户仍能查询既有任务和任务进度；中断任务进入明确的恢复或可重试状态。
5. 数据库写入失败时不得显示保存成功，也不得回退到浏览器或磁盘保存。
6. 保留现有工作台交互、项目库、资产库和主要 API 语义。

## 2. 明确边界

### 2.1 纳入数据库的数据

- 用户会话
- 工作区当前状态
- 项目及项目摘要
- 项目版本和版本快照
- 当前参考资产关系
- 资产库条目
- 工作流任务
- 工作流状态事件和诊断信息
- 上传资产元数据
- 生成资产元数据
- 所有上传和生成文件的二进制内容
- 文件哈希、MIME、大小、来源任务和创建者

### 2.2 不再作为持久化来源的存储

- 浏览器 `localStorage`
- 浏览器 `sessionStorage`
- IndexedDB
- Python 进程内 `_workflow_tasks`
- `uploads/` 与 `storage/` 下的业务文件

React 组件内存仍可保存当前页面渲染状态，但刷新后必须通过 API 从数据库恢复。

## 3. 方案选择

### 3.1 选定方案：PostgreSQL 分块 BLOB

关系数据使用普通表；文件本体使用资产表和分块表保存。

采用分块 BLOB，而不是单字段大 `BYTEA`，原因如下：

- 上传和下载可以流式处理，避免一次把整个文件载入应用内存。
- 单个分块可以独立校验和重试。
- 可限制单次数据库事务大小。
- 适用于 CAD、PDF、GLB、STEP 等尺寸差异较大的文件。
- 备份和恢复仍以 PostgreSQL 为唯一数据源。

不采用 PostgreSQL Large Object API，以避免额外的对象生命周期、权限和孤儿对象维护成本。

### 3.2 分块规则

- 默认分块大小：4 MiB。
- 小于或等于 4 MiB 的文件仍保存为一个分块。
- 每个文件保存完整 SHA-256。
- 每个分块保存顺序号、大小和 SHA-256。
- 只有全部分块写入且完整校验通过后，资产状态才能从 `uploading` 变为 `available`。
- 失败上传保留为 `failed`，由清理任务在保留期后删除。

## 4. 数据模型

### 4.1 用户会话

`user_sessions`

- `id`
- `user_id`
- `token_hash`
- `created_at`
- `expires_at`
- `revoked_at`
- `last_seen_at`
- `client_metadata`

浏览器只持有随机会话标识的 `HttpOnly` Cookie。数据库只保存其哈希，不保存可直接使用的明文凭证。

### 4.2 工作区状态

`workspace_states`

- `id`
- `user_id`
- `selected_project_id`
- `selected_reference_version_id`
- `selected_reference_asset_id`
- `active_scenario`
- `active_workflow_stage`
- `active_step_index`
- `view_mode`
- `scene_mode`
- `selected_industry`
- `generation_prompt`
- `state_data`
- `version`
- `created_at`
- `updated_at`

每个用户只有一条当前工作区记录。`version` 用于乐观锁，避免多个页面互相覆盖。

### 4.3 项目与版本

继续保留现有项目和版本语义，但将 PostgreSQL 作为唯一事实来源：

- `cocreation_project_histories`
- `cocreation_project_version_histories`

需要把字符串关系逐步改为外键关系，并保留现有业务 ID 的唯一约束。版本中的预览图、生成文件和下载文件改为引用 `assets.id`，不再使用磁盘路径作为真实来源。

### 4.4 工作流任务

`workflow_tasks`

- `id`
- `user_id`
- `project_id`
- `version_id`
- `status`
- `progress`
- `current_step`
- `input_payload`
- `design_spec`
- `outputs`
- `diagnostics`
- `error_code`
- `error_message`
- `attempt`
- `recoverable`
- `lease_owner`
- `lease_expires_at`
- `created_at`
- `started_at`
- `completed_at`
- `updated_at`

`workflow_task_events`

- `id`
- `task_id`
- `sequence`
- `event_type`
- `status`
- `progress`
- `message`
- `event_data`
- `created_at`

任务表保存当前快照，事件表保存状态演进。每次进度变化必须在同一事务中更新任务快照并追加事件。

### 4.5 资产与文件内容

`assets`

- `id`
- `user_id`
- `project_id`
- `version_id`
- `task_id`
- `kind`
- `filename`
- `extension`
- `content_type`
- `size_bytes`
- `sha256`
- `chunk_size`
- `chunk_count`
- `status`
- `source`
- `metadata`
- `created_at`
- `updated_at`

`asset_blob_chunks`

- `asset_id`
- `chunk_index`
- `size_bytes`
- `sha256`
- `content`
- `created_at`

唯一约束为 `(asset_id, chunk_index)`。删除资产时通过外键级联删除全部分块。

资产引用全部通过 `asset_id` 完成。为了兼容现有前端，API 可以继续返回下载 URL，但 URL 指向数据库流式下载接口。

## 5. 认证与浏览器行为

### 5.1 Cookie 会话

- 登录或 SSO 交换成功后，后端创建数据库会话。
- 后端设置 `HttpOnly`、`Secure`、`SameSite` Cookie。
- 开发环境允许通过配置关闭 `Secure`，生产环境强制启用。
- 前端请求使用 `credentials: "include"`。
- 前端 JavaScript 不读取或保存认证 Token。
- 登出时后端撤销数据库会话并清除 Cookie。

### 5.2 SSO

- URL `sso_token` 和 `platform_token` 只作为一次性交换材料。
- 前端收到后立即调用后端交换接口，并立即从地址栏移除。
- iframe `postMessage` Token 只存在于消息处理调用栈中，不写入任何浏览器存储。
- 交换完成后依靠 HttpOnly Cookie 维持会话。

### 5.3 前端业务状态

- 删除 `authStorage.ts` 的持久化职责。
- 删除项目、版本、参考资产和资产库的浏览器存取函数。
- 页面初始化并行请求用户、工作区、项目和资产数据。
- 用户操作先提交后端，成功后以响应数据更新 React 状态。
- 写入失败时保留未保存提示，不在浏览器建立备用副本。
- 页面刷新后完全从 API 恢复。

## 6. 后端任务执行与恢复

### 6.1 任务创建

创建工作流时：

1. 校验用户及输入资产所有权。
2. 在事务中创建 `workflow_tasks` 和首条事件。
3. 提交事务后启动执行器。
4. 返回数据库任务快照。

### 6.2 任务更新

所有 `_update_task` 调用改为数据库事务更新。API 查询直接读取数据库，不依赖进程内字典。

执行器通过租约字段防止同一任务被多个进程重复执行：

- 执行前获取租约。
- 运行过程中续租。
- 完成或失败后释放租约。
- 租约过期的 `pending/running` 任务可由恢复扫描器接管。

### 6.3 重启恢复

应用启动时扫描：

- `pending`：重新排队。
- `running` 且租约过期：标记为 `interrupted`，若 `recoverable=true` 则重新排队。
- 已完成但历史版本未创建：补偿写入版本。
- 资产处于 `uploading` 且超时：标记为 `failed`。

第一阶段可以使用数据库任务表加应用内执行器，不额外引入 Redis。多实例通过数据库租约保证互斥。

## 7. 文件上传与下载

### 7.1 上传

上传接口按流读取请求体并逐块写入数据库：

1. 创建 `uploading` 资产记录。
2. 分块写入 `asset_blob_chunks`。
3. 同步计算完整文件和分块 SHA-256。
4. 校验声明大小、实际大小和分块数量。
5. 校验通过后更新资产为 `available`。

任一步失败时资产标记为 `failed`，不会被工作流消费。

### 7.2 下载

下载接口：

1. 校验当前用户对资产的访问权限。
2. 按 `chunk_index` 顺序读取。
3. 通过 `StreamingResponse` 输出。
4. 设置正确的 `Content-Type`、文件名和长度。
5. 支持完整文件下载；Range 请求作为后续兼容项，不作为本次上线前置条件。

### 7.3 业务服务

必须改造当前直接写文件的服务：

- ForgeCAD 导入文件
- ForgeCAD 脚本与导出结果
- 工程图 SVG、PDF、DXF
- Zoo 下载的 GLB、STEP
- 图片精修结果
- 工业设计生成资产

如果外部 CLI 强制需要文件路径，允许在受控临时目录中短暂落盘：

- 临时文件不作为持久化来源。
- 使用完成后立即删除。
- 最终输入和输出必须写入数据库。
- 数据库写入成功后任务才能报告对应资产成功。

## 8. API 设计

保留现有主要入口：

- `POST /api/v1/industrial-design/workflows`
- `GET /api/v1/industrial-design/workflows/{task_id}`
- `/api/v1/cocreation-history/*`
- `/api/v1/forgecad/*`

新增或调整：

- `GET /api/v1/workspace`
- `PUT /api/v1/workspace`
- `GET /api/v1/assets`
- `POST /api/v1/assets/upload`
- `GET /api/v1/assets/{asset_id}`
- `GET /api/v1/assets/{asset_id}/download`
- `DELETE /api/v1/assets/{asset_id}`
- `POST /api/v1/workflows/{task_id}/retry`
- `POST /api/v1/auth/logout`

旧的文件下载 URL 在迁移期返回重定向或代理数据库资产，最终统一为按 `asset_id` 下载。

## 9. 数据迁移

### 9.1 SQLite

提供幂等迁移命令：

1. 读取现有 SQLite 项目和版本。
2. 写入 PostgreSQL。
3. 保留原业务 ID。
4. 重复执行不会制造重复数据。

### 9.2 磁盘文件

扫描现有 `uploads/` 和 `storage/`：

1. 根据数据库路径引用和目录清单创建资产记录。
2. 分块导入文件本体。
3. 校验大小和 SHA-256。
4. 更新版本和任务中的资产引用。
5. 输出无法关联、缺失或校验失败的报告。

迁移程序不自动删除旧文件。完成验收并获得单独授权后才能清理。

### 9.3 浏览器旧数据

正式代码不再持续读取或写入浏览器存储。若必须保留既有浏览器数据，提供一次性、用户主动触发的导入页面：

- 只读取既有键。
- 将数据提交到后端并确认写库。
- 导入完成后清除旧键。
- 新业务代码不再依赖这些键。

该一次性迁移工具不是持久化方案，可在迁移窗口结束后移除。

## 10. 错误处理

- 数据库不可用：API 返回明确的服务不可用错误，前端显示未保存。
- 事务冲突：工作区通过版本号返回冲突，前端重新加载最新状态。
- 资产校验失败：资产不可下载、不可用于工作流。
- 任务执行中断：数据库保留最后状态和事件，可恢复或重试。
- 外部模型失败：诊断信息入库，成功资产不回滚；任务依据有效产出决定完成或失败。
- 历史写入失败：任务不得伪装成已完整归档，记录补偿状态。
- 越权访问：所有项目、任务和资产查询都必须按当前用户过滤。

## 11. 测试与验收

### 11.1 后端测试

- PostgreSQL 模型和迁移测试
- 工作区读写和乐观锁测试
- 任务创建、更新、事件顺序和用户隔离测试
- 任务重启恢复和租约接管测试
- BLOB 单块及多块上传下载测试
- 文件大小、完整 SHA-256 和分块 SHA-256 校验测试
- 资产级联删除测试
- Cookie 登录、SSO 交换、登出和过期测试
- 数据库异常和事务回滚测试
- SQLite 与磁盘资产幂等迁移测试

### 11.2 前端测试

- 页面刷新后从 API 恢复工作区
- 项目、版本、资产和参考资产仅使用 API
- 保存失败时显示未保存
- Cookie 模式请求携带 `credentials: "include"`
- 登录和登出不读写浏览器存储
- 静态扫描业务代码中不得存在 `localStorage`、`sessionStorage` 或 IndexedDB 使用

### 11.3 端到端验收

1. 登录后创建项目并生成版本。
2. 上传超过一个分块大小的文件。
3. 验证数据库包含项目、版本、任务、资产元数据和全部文件分块。
4. 重启后端。
5. 刷新并重新登录，恢复工作区、项目、版本和资产。
6. 下载资产并对比上传前后的 SHA-256。
7. 创建长任务，在运行中重启后端，验证任务进入恢复或可重试状态。
8. 清空浏览器站点数据后重新登录，仍能恢复全部业务数据。
9. 检查浏览器存储，确认不存在认证和业务数据。
10. 临时断开 PostgreSQL，确认页面明确提示失败且不建立本地副本。

## 12. 实施顺序

1. 引入 PostgreSQL 配置和数据库迁移工具。
2. 建立会话、工作区、任务、事件、资产和分块表。
3. 先实现资产存取服务与完整性测试。
4. 将认证改为数据库会话与 HttpOnly Cookie。
5. 将工作流任务从内存迁移到数据库。
6. 将业务文件写入改为资产服务。
7. 增加工作区、项目、版本和资产 API。
8. 移除前端浏览器持久化并接入新 API。
9. 实现 SQLite、磁盘和一次性浏览器迁移。
10. 执行全量测试、重启恢复和端到端验收。

## 13. 不在本次范围内

- 离线编辑
- 浏览器本地草稿
- CDN 或对象存储
- Redis/Celery 队列
- PostgreSQL Range 下载优化
- 自动删除旧 SQLite 和磁盘文件

这些能力不得作为绕过“所有持久化数据必须入 PostgreSQL”的替代方案。
