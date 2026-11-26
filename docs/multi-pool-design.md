# 多池架构：设计决策与实施台账

> 2026-11-06 设计并实施。本文档自包含，供任意后续会话接续工作。
> 配套检索：local-memory 记忆 `c4a712e2`（设计全集）、`db5ced24`（实施进度）、`f7c3906d`（外部参考：双脑记忆系统文章）。

## 1. 背景与目标

local-memory（lm）是**中立的记忆基础设施**，不绑定任何模型——所有模型都是调用方。「多模型」的正确含义是：**多个职责化 agent 各持独立记忆池，共享同一套 lm 底座**。外部参考（双脑记忆系统文章，jihua3445.github.io/multi-model-dual-brain-memory-system）的「多模型分工协作同一记忆」与本项目方向不同，仅借鉴其治理思想（主导权=批准权、材料边界、supersedes 版本链）。

## 2. 核心设计决策（已拍板，2026-11-06 多轮澄清）

| # | 决策 | 内容 |
|---|------|------|
| D1 | 池划分维度 | agent **工作职责**，与用什么模型无关（模型只是可替换的调用方） |
| D2 | 组织约束 | 同项目多 agent 必为上下级或分工服务同一上级；**无平级共管**；项目事实权威在上级 |
| D3 | 上下级通道 | subagent 任务/结果通道（类比文章 ACT/RESULT），**池互不直读**，上级只做决策 |
| D4 | 共享池 | default 池兼任共享池（装跨项目公共记忆），不另建 |
| D5 | 审计模型 | **先写后审计，全池统一**：agent 写入立即生效 + `audited=false` 标记；用户人工审计后消标记；无标记的存量记忆视为已审计 |
| D6 | grooming 拍板权 | **下放各池 owner agent**：建议制保留（不自动执行），拍板主体从用户换成各 agent 自己；用户退到宪法层（agent create/权限表/共享池规则）+ 事后审计 |
| D7 | 职责边界物化 | 在**池级写权限**（agent 写自己的池+读共享池）；category 不做权限轴（会切碎职责），留作池内检索配比与 grooming 策略 |
| D8 | 性格 | 非标配。多数职责 agent 是纯执行体；仅 1-2 个助手级情绪价值 agent 涉及性格（性格=各自池积累长出，非 prompt 写死） |
| D9 | 模型关联两层 | 干活模型挂宿主 agent 定义（lm 无感）；grooming 模型挂池 config.json 的 llm 块（钉死固定模型仅是降低人审噪音的优化，非必需） |
| D10 | 约定分发 | 待定。挂点候选：跟池走（池目录带约定文件）或跟 agent 定义走（agent md 自带）；reviewer 试点采用「跟 agent 定义走」 |
| D11 | 全局规则分层 | 宪法层（身份中立，所有 agent）/ 助理层（仅用户助理主会话）；分层标注在 CLAUDE.md，职责 agent 与助理层冲突时以职责约定为准 |
| D12 | 口吻风格 | 记忆写入风格是职责约定的一部分（事实型保留 why+教训；聊天类 agent 不需要 how/why），在各 agent.md 定义 |

## 3. 架构与机制现状

### 3.1 身份与池路由

- **agent registry**：`~/.memory/registry.json` 新增 `agents` 段。现有三个身份：
  - `assistant` → default 池（兼共享池），角色=user-assistant
  - `reviewer` → review 池，角色=daily-review
  - `coder` → coding 池，角色=coding（宿主 agent 定义文件中约定；编码域记忆=bug 根因/反直觉技术事实/实现决策/踩坑，`project` 填项目标识；存量编码记忆不迁移，共享池只读可检索，分工边界=谁干活谁写）
- **池绑定三层解析**（已有机制）：`MEMORY_POOL` env → 仓库级 workspace config.pool → registry.active_pool
- **请求级路由**：`memory_paths.workspace_pool_scope(pool_id)`（ContextVar）+ 新增 `set_pool_context/reset_pool_context`（跨 Flask 钩子用）

### 3.2 mcp_server 新能力

- `add_memory(..., agent='')`：
  - agent 非空 → 校验 registry.agents（**未登记身份拒绝写入**）→ `workspace_pool_scope` 路由到绑定池 → metadata 合入 `agent` + `audited=false`
  - 写入失败进 pending 时留痕同样合入；`retry_pending` 按 pending 条目 `metadata.agent` 反查池路由（此前只会扫 active 池）
  - `audited` **强制覆盖为 False**（agent 显式传 true 也拦下）
  - 不传 agent → 行为不变（写 active 池，assistant/用户完全兼容）
- `search_memory(..., pool='')`：校验池存在 → scope 包住检索；空 = active 池

### 3.3 mem_viewer 新能力（升级于第 2.5 步）

- toolbar 池下拉（`/api/pools`、`POST /api/switch_pool`），切换全局生效、chroma 缓存失效重取
- 列表按 `updated_at` 降序；未审计紫色 badge；详情「审计状态」区 + 「确认审计」按钮（`POST /api/audit/<id>`，置 audited=true + lineage 记 AUDIT 事件）
- 有 merged_from 的条目显示绿色「有演变」badge（演变时间线详情面板原本就有）
- 非 active 池浏览时显示只读横幅 + 写路由 403（`require_active_pool` 装饰器，7 个写路由）

### 3.4 全局规则分层

`<宿主配置>/CLAUDE.md`（多端已 sync）：头部分层说明 + 「仅助理主会话」标注。P0-1 改写为「改用户的产物先确认；agent 对自己池的记忆维护按自治授权执行」。**职责 agent 派发节**：编码类任务默认派 coder（豁免=typo/单行级、主会话已持有全部上下文的小运维改、纯文档/记忆操作）+ 收工对照 expected_outcome 审查。

### 3.5 宿主 agent 定义

`<宿主 agents 目录>/reviewer.md`：身份约定（add_memory 必带 agent='reviewer'、素材 pool='default'、自己池 pool='review'、共享池只读）、职责流程（引用 daily-review SKILL.md）、记忆口吻协议（事实型保留 why+教训、结论性发现才写池、升格建议只进复盘文档）。

`<宿主 agents 目录>/coder.md`：与 reviewer 同构——身份约定（agent='coder'、共享 pool='default' 只读、自己池 pool='coding'）、职责流程（任务由主会话派发、动手前双池检索、范围外动作报告不擅自做）、记忆协议（episodic|reference、project 填项目标识、取代清理义务）。

### 3.6 派发/汇报协议（ACT/RESULT，2026-11-07 拍板「轻+中」）

借鉴双脑文章（f7c3906d）的 ACT/RESULT 通道与协议级排除思想，在宿主只收自由文本 prompt 的约束内落地：

**ACT 派发模板**（assistant 派发子代理时套用）：

- 任务：<做什么>
- 项目/路径：<绝对路径>
- 边界/红线：<范围外禁做项；git 写/删、凭据、批量写删须清单>
- expected_outcome：<完成后应看到什么，须可验证>
- 未知声明：查不出的部分明说未知，不编造

**RESULT 汇报协议**（写进各 agent md 的「汇报协议」节）：实际结果 vs 预期逐项对照 / side_effects（范围外动过的文件/配置/记忆）/ 未解决项明说未知不编造。

**协议级变体**：agent md 约定「任务 prompt 缺边界/红线 → 先回报缺字段等补全，不自行猜测动手」——子代理反向强制派发方写全，是自由文本约束下最接近协议级排除的形态。reviewer 例外：复盘任务 expected_outcome 缺省走默认预期（派「复盘」两字不卡缺字段），coder 无例外必须写全。审查收工报告时对照 expected_outcome，偏差大用 SendMessage 追问（EXPAND 思想）。未照搬：七种消息类型（我们只有两级，ACT/RESULT/EXPAND 三概念够用）、代签密钥（无宿主支持）。

### 3.7 active_pool 现状定位（2026-11-10 梳理）

对 **agent 侧已无意义**：四工具（add/search/delete/confirm）+ transfer_memory 全部 pool 参数化，agent 全链路不依赖切池；switch_pool 约定禁并行使用，active_pool 实际冻结在 default（2026-11-10 实测 registry 值=default；**「冻结」是约定与偶然的现状、非机制保证**——viewer 池下拉即 /api/switch_pool 全局生效，随时解冻并触发 L6 主线注入漂移）。从「工作位置移动句柄」退化为「主线默认池指针」，L6 的多会话互踩引信已被参数化拆除，机制本体保留。

对 **主线/viewer 仍是承重墙**，三个依赖点（2026-11-10 rg 实证）：

1. **无参调用兜底解析链**：`memory_paths.py:59` ContextVar → env MEMORY_POOL → registry.active_pool → 'default'——主线所有不带 pool 参数的 add/search/grooming/snapshot/diff 落点
2. **hook 注入**：memory_hook 无 pool 参数，检索走同一解析链（L6 受害面，实际冻结在 default）
3. **viewer 写护栏**：`mem_viewer.py` `require_active_pool`——写操作仅限 active 池，非 active 只读+403；池下拉即 switch_pool

**退役路径**（挂在第 6 节路线表，勿单独拆）：hook 多池化落地 → ②消失；viewer 改按浏览池写 + grooming 按池下放（D8）→ ③①收缩；HTTP token 身份按连接绑池 → 彻底退役。现在单独拆不安全——主线无参调用会失去落点。

## 4. 已实施清单

| 文件 | 改动 | 状态 |
|------|------|------|
| `src/mcp_server.py` | agent/pool 参数 + 校验/路由/留痕 + retry 路由 | 仓库+runtime 已同步，**未 commit** |
| `src/mem_viewer.py` | 池动态化 + 3 新 API + 前端升级 + 请求级池路由 + 写护栏 | 同上 |
| `src/memory_paths.py` | `set_pool_context`/`reset_pool_context` 公开 helper | 同上 |
| `src/hybrid_search.py` | `get_chroma_client` 改按 chroma_db 路径缓存（原进程级单例跨池污染，同时修复 mcp search 撕裂） | 同上 |
| `<宿主 agents 目录>/reviewer.md` | 新建 | 生效（宿主已加载） |
| `<宿主 agents 目录>/coder.md` | 新建（第 2 个职责 agent） | 生效（新会话加载后出现在 agent 列表） |
| `<宿主 agents 目录>/coder.md` + `reviewer.md` | 追加「汇报协议（RESULT）」节 + 缺字段先报告约定（见 3.6 节） | 生效（下次派发即用） |
| `<宿主 agents 目录>/reviewer.md`（另一容器注册副本） | hardlink 自前一文件（同 inode，双容器注册，改一处两处生效）。注意：任一侧用「临时文件+rename」式原子重写编辑会静默断链，改后 `ls -i` 验证同 inode（已实证发生一次并重建） | 生效（新会话加载） |
| `src/redact.py`（新）+ `src/memory_store.py` + `src/mcp_server.py` | **周末改造①写入脱敏**（2026-11-08/09，dsh-memory 横评落地）：redact.py 9 类凭据模式统一 `[REDACTED:label]`，主写入路径在 memory_store.add 收口；**修复 pending 降级队列明文落盘敏感内容的泄漏**（写入侧根治，非事后清洗存量） | 仓库+runtime 已同步，**未 commit**（随首次整版本一次性提交） |
| `src/memory_hook.py` | **周末改造②召回可观测**：diagnostics_out 字段 + `MEMORY_HOOK_DEBUG=1` 写 last_recall.json 召回快照（失败不影响 hook）——回答「为什么这条被召回」 | 同上 |
| `src/hybrid_search.py` + `src/memory_sync.py` + `tests/test_recency.py`/`test_redact.py`（新） | **周末改造③recency 半衰期回血**（借鉴 familiar-memory）：active_memories 加 last_accessed_at 列（存量库 ALTER 补列）+ 批量 load/touch 接口，命中记忆 bonus=0.006×0.5^(days/30)（半衰期 30 天）回血折半；配套单测 82+143 行，73 测试绿 | 同上 |
| `src/memory_store.py` | **L8 修复**（2026-11-09）：`_chroma_upsert` 补传 `documents=[content]`，新写入不再产生空 documents（详见 §5 L8，含存量回填与 6 条坏向量重建） | 仓库+runtime 已同步，**未 commit**（随首次整版本提交）；生效=下个会话 MCP 重启 |
| `~/.memory/registry.json` | agents 段 + coding 池；备份在 `~/.memory/backups/registry.json.bak-20261106`、`.bak-20261106-2` | 生效 |
| `~/.memory/runtime/`（5 文件） | 2026-11-06 下午补同步陈旧副本：llm_client/memory_delete/memory_lineage/memory_sync（9 月 23 日旧版）+ sqlite_conn（缺失）——均为已入库 HEAD 版本；同步规则教训见 L7 | 生效（运行中进程不受影响，重启后生效） |
| `src/mcp_server.py` + runtime | `delete_memory`/`confirm_grooming` 加可选 `pool` 参数（校验+scope，2026-11-06 傍晚，根治 L6 切池依赖） | 仓库改动**未 commit**；runtime 已同步，工具层下个会话生效 |
| `<宿主配置>/CLAUDE.md` + 多端 | 分层标注 | 生效（下个会话加载新版） |

审查修复记录：mcp_server 3 项（pending 留痕、audited 强制、retry 路由）；mem_viewer 4 项（写路径撕裂、存量误标、memory_sync 常量绕行、chroma client 跨池单例）。**实测验证**：audited 强制覆盖、池隔离（default 895/review 0）、写护栏 403、只读横幅、mcp 双池检索回归。**代码级修复未实测**（需注入 chroma 失败场景）：pending 失败留痕、retry 池路由——列入 reviewer 首轮实战观察点；存量误标修复为语义推演+badge 源码间接验证。

## 5. 已知限制与技术债

| # | 限制 | 影响 | 触发处理时机 |
|---|------|------|------------|
| L1 | 参数级身份是逻辑边界：agent 不带 agent 参数仍可写 active 池 | 挡乱带、不挡谎报 | 第 3 步写网关（材料边界）收口；过渡靠审计留痕 |
| L2 | `migrate_active_if_needed` 对「空表+chroma 有数据」会全量导入 | 批处理脚本切池跑有灌库风险（viewer 已规避：新代码不调它；mcp 链路不触发） | 给 migrate 加池 guard（建议：仅 default/active 池执行） |
| L3 | history.db / lineage.jsonl 未池化 | 非 active 池的演变时间线显示 default 的历史 | 池化改造后置；review 池早期无历史，影响小 |
| L4 | viewer 写操作仅 active 池可用 | 非 active 池只读浏览（已用横幅+403 明示） | 写路径池化改造时放开 |
| L5 | 本会话 MCP server 进程是旧代码 | **agent 参数在新会话才生效** | 新会话自动解决 |
| L6 | **active_pool 是全局单例，多会话并发互踩**（2026-11-06 实锤）：两个宿主会话各自 spawn MCP server，共享 registry，一方 `switch_pool` 几秒内被另一方覆盖 | 依赖切池的操作（delete/confirm_grooming 等 active 池限定操作）在并行会话下不可靠；**hook 注入同样受害**——`memory_hook.py`/`hybrid_search` 无 pool 参数（2026-11-06 读码实锤），注入查询的是 registry.active_pool 当前值，并行会话切池后主会话开场注入内容漂移到别的池 | 短期：错开多会话实战；根治：delete/confirm_grooming 已加 `pool` 参数（2026-11-06 傍晚落地，见第 6 节路线表），add/search 本就走 scope，四工具齐后 agent 全链路不再依赖切池；远期：HTTP token 身份时按连接绑池 |
| L7 | runtime 同步规则只覆盖「本次改动的文件」，**已入库但从未同步的文件会陈旧**：memory_sync 等 4 文件停在 9 月 23 日、sqlite_conn 缺失（2026-11-06 已补齐对齐 HEAD） | 旧快照兜底路径在新池上抛 no such table；新旧代码行为漂移 | 每次同步 runtime 时先全量 diff src/*.py，不只 cp 本次改动文件 |
| L8 | **chroma documents 主写入路径为空**（2026-11-09 审计发现，HEAD 即如此非本次回归）：`memory_store._chroma_upsert` 只 upsert ids/embeddings/metadatas（全文在 chroma metadata `data` 键里），不传 `documents`；`pool_manager.transfer_memory` 则两处都写。default 池 826/863 条 documents 空（非空的 37 条来自 transfer/恢复脚本），review 池新写入 2 条同样空 | 全仓 src 无任何读侧消费 documents（内容显示/检索全走 sqlite，vec 用 embedding），主链路无损；风险=直接读 chroma documents 的外部工具拿到空文（本次审计即误报过一次）、未来重构踩坑、同池内 transfer 条与 add 条 documents 不一致 | 修复一行（`col.upsert(..., documents=[content])`）+ 存量回填——**已完成**（2026-11-09 午后用户拍板）：代码修复入库+runtime 已同步（**下个会话 MCP 生效**，本会话内新增记忆 documents 仍空）；存量回填完成——default 864/review 9 全部 documents 非空且与 sqlite 一致、向量 1024 维零污染（回填显式自带 embeddings，绕开 chroma 默认 ONNX 嵌入函数——教训：update 只传 documents 会触发 79MB 模型下载）；进程被杀留下的半应用队列由后续进程压实补齐（约 505 条 documents 由此恢复、向量未动——机制为推断，未经 chroma 源码证实，终态已实测验证）。顺带发现并重建 **6 条陈旧坏向量条目**（get 带 embeddings 即报 Error finding id：2 条 2026-09-20、4 条 2026-10-29 10:21 同秒批量，与本次无关的潜伏损伤，已按 sqlite 权威内容+embed_text 重建）。回填前备份 `~/.memory/backups/backfill-docs-20261109/`；MCP 实链路检索回访正常；回填后测试套件 73 passed 复跑确认 |

### 5.5 事故记录

**2026-11-06 18:27-18:33 review 池误迁移事故（当日恢复）**：并行会话跑 actor=`migration` 脚本，把 115 条（43 条活记忆 + 72 条已删除的历史尸体，全部不带 agent 身份/audited 标记，违反写入约定）灌入 review 池（全零池被污染），并从 default 删除 44 条（43 条活记忆 + 1 条它自造的「test: review 池写入验证」垃圾条目，垃圾未进 review 池、随 archive 清理消失）。当日 18:40 恢复：按内容哈希桥把 43 条活记忆迁回 default（原 id 取自 archive、原 created_at/updated_at 从 history ADD 事件重建、向量从 review chroma 复制、metadata 保真）、review 池清零（sqlite+chroma，history 的 migration 事件保留作审计痕）、archive 清空 18:30 窗口 44 行、lineage 记 43 条 RESTORE；终态 default=861 / review=0（sqlite+chroma 双侧一致）。备份在 `~/.memory/backups/restore-20261106/`。

教训：①**池间批量迁移/删除类操作必须先出人工审核清单再执行**，禁止 agent 无人审批量写删（本次把 deleted_archive 的尸体也灌进活池，正是无清单的后果）；②record_event 白名单缺 RESTORE 动作（静默 fallback 成 GROOMING，已补枚举+同步 runtime）；③lineage.jsonl 混入过裸文本脏行（消费方 try/except 容错无害，已清）——append-only 账本也要偶发校验 JSON 完整性。

## 6. 待办路线（按触发条件，非时间表）

| 待办 | 触发条件 | 说明 |
|------|---------|------|
| **reviewer 实战** | ~~新会话即可~~ **首跑已通过**（主线派发收缩跑：窗口约 26 分钟，跳过 auto_promote/grooming/snapshot/record-scan-end） | 验证实证：①写入落 review 池带痕——写入条目均 agent=reviewer + audited=False（主线经 workspace_pool_scope('review') 查 chroma 确证，reviewer 自查无该查询口）；sqlite content 完整；chroma documents 为空属 L8 长期行为，非 agent 路由回归；②共享池只读——default 池中 agent=reviewer 为 0；③复盘文档产出独立文件，未碰 cron 文档。RESULT 协议生效（一次脚本假空偏差被 list-sessions 交叉验证自纠，坑已沉淀入池）。未覆盖：pending 失败留痕、retry 池路由两代码修复点（本次无失败场景），留后续实战观察；record-scan-end 未跑，窗口由下次复盘自然补扫。同日午后用户拍板直接接 cron（见「复盘 cron 迁移」行）。 |
| **coder 实战** | 宿主加载 coder.md 后 | 首个编码任务派 coder；验证点同 reviewer：落池带痕、双池检索、宪法层约束（git 写/凭据/范围外报告） |
| git commit | 用户拍板 | 仓库 4 文件改动 + CLAUDE.md 三端同步均未提交 |
| ~~第 2 个职责 agent~~ | ~~按需~~ | **已完成**（2026-11-06 下午，coder/coding 最小落地；用户拍板 agent create 打包命令延后——等第 3 个 agent 出现真实重复需求再做，避免为两次使用写抽象） |
| MCP 工具加 pool 参数 | ~~用户拍板~~ **已完成**（2026-11-06 傍晚拍板并落地） | `delete_memory`/`confirm_grooming` 加可选 `pool` 参数：校验池存在 + `workspace_pool_scope` 包住操作，与 search/add 同构；agent 删自己池/确认 grooming 不再依赖切池，L6 根治。烟测：非法池拒绝、active 占用在 review 时 pool='coding' 删除精准命中且全局不扰动。**工具层下个会话生效**（运行中 MCP 进程未热加载）；confirm_grooming 带 pool 未实测（与 delete 同构推定可用） |
| transfer_memory 转池接口 | ~~设计~~ **已完成**（用户拍板接口设计+授权首批迁移） | `pool_manager.transfer_memory(memory_id, source_pool, target_pool, mode='move'\|'copy', reason, actor)` + MCP 同名工具：move=完整保真写入目标池（原 id/时间戳/向量/grooming 元数据）后物理删源池 sqlite+chroma+history 事件+lineage 旧条目（archive 不留行），源池 lineage 留 1 条 TRANSFER 审计痕（含 purged 统计）；copy=纯复制（双存，检索双命中）。烟测四用例（非法源池/冲突拒绝/move 全链清理/copy）+ 首批实战：7 条复盘域记忆 default→review（用户授权 AI 自行甄别），终态双侧 chroma 一致，review 池检索验证通过。chroma client 必须走 hybrid_search.get_chroma_client 缓存（自建 PersistentClient 会 settings 冲突）。工具层下个会话生效 |
| hook 多池化 | 待用户拍板设计 | memory_hook 层循环多池各自调 hybrid_search 合并结果（hybrid_search 零改动，最小方案）；落地后 coding 池编码存量迁移解锁（实测候选数百条=工作项目类，须用户过目清单） |
| 复盘覆盖子代理会话 | coder 实战积累 1-2 周后评估 | 现状（用户拍板 C 方案先跑）：宿主子代理转录复盘扫描明确排除（子代理转录不通配扫到）；coder 工作靠主会话转写 default 池间接覆盖。缺口=coder 写进 coding 池的技术细节复盘不读（reviewer 不碰 coding 池）。备选：B=reviewer 复盘时读 coding 池（跨池审计，成本低，需澄清 D3 边界）；A=复盘扫子代理转录（覆盖全但 token 成本高，双宿主都要改） |
| 写网关完整版 | HTTP token 身份落地时 | 写路径按身份强校验（材料边界）；共享 MCP 进程无法区分调用方，参数级+审计留痕是当前实际边界（2026-11-06 复核） |
| grooming 拍板下放 | review 池有积累 | grooming 建议队列对接各 agent 的 confirm_grooming 调用 |
| history/lineage 池化 | 非 active 池需要看演变 | L3 |
| migrate 池 guard | 随下次批处理改造 | L2 |
| HTTP token 身份 | NAS Docker 部署 | 每 agent 一 token，传输级身份，谎报空间归零 |
| ~~复盘 cron 迁移~~ | ~~reviewer 实战稳定~~ **已完成**（2026-11-09 午后用户拍板提前：实战攒经验优于等稳定） | 方式=改 SKILL.md「对话深度分析」步为派 reviewer（ACT 五字段，结论写 review 池，主线组装文档与全部机械环节，派发失败重试一次即 inline 兜底）；cron prompt 不动（本就委托 SKILL.md）；reviewer.md hardlink 双容器注册。首次全量实战=下次 cron（工作日 09:33），观察点：单 agent 全量扫描 token 成本（收缩跑基线=3 会话 143 万）、巨无霸处理、降级路径 |

## 7. 关键文件与索引

- 设计全集记忆：`c4a712e2`（八轮澄清）；实施进度记忆：`db5ced24`；双脑文章参考：`f7c3906d`
- 池机制：`src/pool_manager.py`（list/switch/create/clone + **transfer_memory 转池**，无 CLI，库函数）；`src/memory_paths.py`（ContextVar/LazyPath/scope）
- 检索：`src/hybrid_search.py`（chroma client 按 path 缓存、vector_search 走 ContextVar、recency 半衰期 bonus）
- 写入脱敏：`src/redact.py`（9 类凭据模式，memory_store.add 收口）；hook 召回 debug：`MEMORY_HOOK_DEBUG=1` → runtime 下 last_recall.json
- viewer：`src/mem_viewer.py`（`_current_pool_id`/`_active_db`/`_history_db`/`require_active_pool`/`before_request` 池钩子）
- runtime 副本：仓库 src 改动后必须 `cp` 同步 `~/.memory/runtime/`（MCP server 与 viewer 都跑 runtime 副本，PYTHONPATH=runtime）；同步时全量 diff src/*.py（L7 教训）
- review 池当前状态：reviewer 首跑写入的条目均带 agent='reviewer' + audited=false 待 mem_viewer 审计；documents 已随 L8 回填非空；config.json 从 default 模板复制（auto_follow）
- coding 池当前状态：从零开始，config.json 从 default 模板复制；**存量编码记忆不迁移**（拍板记录）：default 池中编码类占 70%+，主会话 `memory_hook.py`/`hybrid_search` 无 pool 参数，注入查询的是 registry.active_pool 当前值（单会话=default，多会话下被并行切池干扰，见 L6），迁移即断供日常修 bug 的开场自动注入；而 coder 读存量走双池检索本就覆盖，迁移无功能增量。复制亦被否（双份权威+检索重复挤前排+取代清理跑两遍）。将来 hook/检索多池化落地时再迁，届时迁移清单须用户过目（存量是编码坑/偏好/工作流混合体，不能按 project 一刀切）

## 8. 新会话接续指南

1. Read 本文档；需要设计细节再 `search_memory`（关键词：多池/agent 职责/审计标记）
2. 确认 MCP server 已重启（新会话即满足）；`mcp__local-memory__list_pools` 应显示 default + review + coding 三池
3. 从第 6 节路线表选任务；当前待做：coder 实战（reviewer 已于 2026-11-09 接入 cron 常态运行；MCP 工具 pool 参数已于 2026-11-06 傍晚落地，工具层随本会话重启生效）
4. 改仓库代码后：`py_compile` → `cp` 同步 runtime（**全量 diff src/*.py**，L7 教训）→ 烟测；registry 改动前先看 backups 备份
5. git 写操作等用户明确口令（改动未提交是已知状态，勿自行 commit）
6. 多会话并行时勿依赖 `switch_pool`（L6 互踩）；需要非 active 池的删除/维护，用独立进程 `workspace_pool_scope` 包住执行，不碰全局 registry
