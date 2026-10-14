# 执行与验收记录

日期：2026-10-14（Asia/Shanghai）。项目根目录为本文件的上一级。沿用已有 Python 3.12.11 / lang_env，MCP 保持 1.18.0；本次未创建环境或升级包。httpx 0.28.1 和 jsonschema 4.25.0 已存在，作为新 Agent 的直接依赖记录进 requirements。

## 本次修改

- 新增 DeepSeek Chat Completions 传输、模型工具调用循环、单次/交互式 Agent CLI。默认 deepseek-flash，环境变量可配置。
- Agent 从真实 MCP 发现 schema，由模型提出调用，通过 MCP 执行并回传；Fetch 只接受当前问题已成功 Search 的 ID。来源单独展示，重复父块使用标明缓存来源的结果。
- 保留轮次、调用次数、模型请求、MCP 调用及初始化超时；截断、过滤、中断、缺证据和服务错误均不输出成功回答。失败保留脱敏部分轨迹。
- 新增薄 run.py 统一入口，以及不依赖模型/索引的固定数据 M0 协议示例。默认运行仅帮助，避免无参数重建。
- DeepSeek Key 仅由运行环境提供，检索 Server 不继承该 Key；配置摘要、HTTP 异常和轨迹不回显密钥。
- 更新最简 README 与已确认 PLAN，清理重复讨论稿和临时产物。语料、BGE-M3、实际索引及正式 qrels 未修改。

## 实际执行结果

以下命令使用原开发机器已验证的解释器运行，等价于激活该环境后执行：

| 命令 / 检查 | 结果 | 范围 |
| --- | --- | --- |
| python run.py test -q | 132 passed in 37.82s，exit 0 | 原有 71 项及新增 Agent/HTTP/入口测试；模型请求使用明确标记的离线 fixture |
| python run.py check | F1/F2/P1/P2/P3 全部 PASS，exit 0 | 真实生产 Server、实际 BGE-M3/FAISS、两工具及共享 Search 一致性 |
| python run.py demo --check | PASS，exit 0 | 真实 stdio initialize/list/call；固定数据、不属于正式检索评测 |
| python run.py agent --check-config，无 Key | EXPECTED BLOCKED，子进程 exit 2 | 不请求 DeepSeek、不启动检索 Server |
| DeepSeekClient + AgentRunner + 生产 MCP | F1/P1 PASS | HTTP 模型响应由 MockTransport 提供，检索 Server 和 BGE-M3/FAISS 为真实生产实现 |
| 输入完整性 | PASS | 300 篇语料和正式 qrels 哈希匹配审核记录，9 个索引/节点/关系文件校验和匹配 manifest |
| 报告完整性 | PASS | 保留 5 组汇总及 750 条正式测量明细 |
| Markdown 相对链接 | PASS | README/PLAN/审核定稿中 115 个相对目标存在；无开发者盘符路径 |

生产 MCP 联调中，模型响应由离线测试代码模拟 Search→Fetch→终答，不能描述成 DeepSeek 已真实选择工具。实际 Search 命中 ID 一致，F1 Fetch 原文 240 字符、P1 Fetch 原文 960 字符，每组实际 tools/call 两次。完整且明确标注来源的记录见 [agent_integration.json](agent_integration.json)。

SDK 1.18 的超时联调存在晚到响应导致 stdio 收尾 BrokenResourceError 的情况；Agent CLI 会保存最初 MCP_TIMEOUT 轨迹并以非零状态退出，该真实失败路径纳入测试，没有将它改写成成功，也没有升级 SDK。

## 保留的正式实验

2026-10-13 已完成两次真实 MCP 五组评测。本次没有改分块、Embedding、检索、指标或金标准，因此没有重建向量和重跑正式评测；主报告保留测试结束后的复测结果，旧的首轮冗余报告按精简要求清理。

- 50 道问题，每组每题预热 1 次、测量 3 次；5 组共 750 条正式明细。
- Search Recall@5：F1/P1/P2/P3 为 0.89，F2 为 1.00。
- Fetch Recall@5：F1 为 0.89，F2/P1/P2/P3 为 1.00。
- P1 相对 F1 增加 11 个百分点；相对 F2 召回持平，工具输出 Token 减少约 47.66%。不声称对 F2 的召回提升。
- 现有 corpus/qrels 为合成语料与统一模板问题；标注方法是助手源文核对加用户批次批准，不是独立人工双标。
- 四套节点及 6210 条父子关系曾从源文重新核对；两个索引含 2670 个有限、归一化的 1024 维向量。历史细节保留在 reproducibility.json、vector_check.json、evaluation_check.json。

主表见 [summary.md](summary.md)，数值见 [results.csv](results.csv)，逐题范围及判断见 [per_query.csv](per_query.csv)。审核与 30 题证据定位修正依据保留在 annotation_review.md、annotation_changes.csv、原始候选及审核定稿中。

## 清理范围

仅清理 new_project 内已核对的目标，所有递归删除前检查解析后的绝对路径位于该项目目录内；没有删除父项目或参考项目文件。

- 合并说明后删除旧 TASK.md、待审查计划副本、快速上手.md、Git交接.md、models/README.md；保留 README.md 与 PLAN.md。
- 删除 reports/构建方法对照.md、旧 git_portability_check.json、initial_run 首轮重复报告。
- 删除已被正式文件替代的 qrels README、待人工审核跳转页、格式示例和修正后候选副本；保留 test.jsonl、review.json、审核定稿及原始候选审计依据。
- 删除临时 Git 导出目录、旧 artifacts/pytest_tmp 与 IDE 配置；测试结束后清理生成的 Python/pytest 缓存。
- 必需的 corpus、完整模型、manifest、FAISS、节点、关系、正式报告与核心测试均保留。

## Docker 实际构建与复现（2026-10-14）

用户要求通过容器简化微信交接后的环境安装。新增 Dockerfile、compose.yaml、.dockerignore、.env.example，并在 README 中保留完整的容器构建、索引、评测和问答命令；原生 Python 路径与实验逻辑未修改。

- 镜像使用 Python 3.12.15 / Linux amd64，官方 Python 基础镜像以本次实际取得的摘要固定；先安装官方 CPU PyTorch 2.7.1，再安装现有锁定依赖；不打包模型或索引。
- BGE-M3 挂载为只读；容器索引单独保存于 `artifacts/docker_cpu/`，报告和轨迹保存在宿主 `reports/`、`logs/`。CPU Torch 元数据为 `2.7.1+cpu`，原 Windows 为 `2.7.1`，首次容器运行须重建索引；没有覆盖原 Windows 索引。
- Key 仅在 Compose 运行时从环境变量或本机 `.env` 提供；示例 Key 留空，构建上下文不含实际环境文件。

| 实际检查 | 结果 | 范围与限制 |
| --- | --- | --- |
| Docker 引擎 / Compose | 可用 | Docker 29.6.1、Compose 5.3.0、Linux amd64；通过授权的 Docker API 访问执行 |
| `docker compose config --quiet` | PASS | 实际 Compose 解析有效；4 个挂载、模型只读、独立容器索引、交互输入、无公开端口 |
| 实际构建上下文导出 | PASS | 使用 `FROM scratch` 和同一 .dockerignore 的临时检查，不下载基础镜像；Docker COPY 导出 2,325,763 字节应用输入 |
| 导出输入完整性 | PASS | 300 篇语料逐文件 SHA256 一致，正式 qrels SHA256 一致；模型、索引、日志、IDE、缓存和 .env 未进入导出 |
| 完整 `docker compose build` | PASS | 用户要求重试后成功取得官方基础镜像，改用 Debian 官方 HTTPS 源并保留签名校验；无全局 Docker 设置修改 |
| `docker run --rm --pull=never kb-mcp:cpu test -q` | PASS | 132 passed in 41.22s；模型调用仍使用明确标记的离线 fixture |
| 镜像内 `demo --check` | PASS | 真实 stdio MCP 固定样例，不依赖模型和索引 |
| Compose `index` | READY | 完整 BGE-M3 / 300 篇语料；独立 CPU 索引的 9 个文件校验通过，语料和模型指纹与原 Windows 一致 |
| Compose `check` | PASS | F1/F2/P1/P2/P3 真实 MCP 全部通过；检查另存到 reports/docker_eval，原 Windows 检查记录保留 |
| Compose `eval --output reports/docker_eval --repeats 3 --warmup 1` | PASS | 五组均 OK，每组 50 题测量 3 次，共 750 条正式记录；预热每题 1 次 |
| 逐题跨环境核对 | PASS | 750 条记录的 Search/Fetch 节点、原文区间和证据判断均一致；召回率和去重原文 Token 一致 |

最初构建曾遇到 Docker Hub TLS 超时和 Debian HTTP 502；重试及官方 HTTPS 源解决后，实际容器测试发现自动安装的 Pydantic 2.14.1 与 MCP 1.18 的内部导入不兼容。现将 Pydantic 2.11.7、pydantic-settings 2.10.1 锁定为原开发环境已有版本，MCP SDK 未升级；修复后的完整容器测试通过。

跨环境相似度分数最大差异为 `3.5762786865234375e-07`；完整工具输出按含 score 数字的 JSON 分词，Tool Token 每题均值相对 Windows 最大差 0.32，逐题差异范围 -8 至 +6。原文及证据覆盖一致，未将浮点数字串变化改写成完全相同的工具 Token。两环境报告分别保留，延迟随运行环境变化。

独立模型 ZIP 已生成，21 个文件共 2,297,543,904 字节，模型及 Tokenizer 指纹与原索引一致。为符合 GitHub Release 单文件限制，模型 ZIP 分为 5 个 512 MiB 上限的分卷，逐卷读回和拼接 SHA256 均已核对。交付的 10 个文件已准备完成，交接清单为 READY。本机新接收目录已从模型分卷合并、加载压缩镜像、解压项目及模型开始，使用打包的 CPU 索引完成五组真实 MCP 检查；重复初始化也通过。此项验证没有重新 Embedding。镜像仓库和 GitHub Release 的发布状态以远程实际页面为准。

## 尚待外部验收

- 实际 DeepSeek 请求尚未运行：用户将后续提供 API Key。配置存在不等于鉴权、额度、网络或真实工具选择已通过。
- 经理/导师的里程碑 Review 尚未确认；测试通过与业务收益验收分别记录。
- M2 是后续扩展，不属于本次必做代码范围。
- 本地已初始化 main 并配置私有目标 ZongshunXu/kb-mcp-project-1；网页登录和远程私有仓库创建已完成。源码和 Release 上传状态以远程实际页面为准。接收方自身机器的运行仍需其确认；本机容器验证与第三方机器验收分开。
