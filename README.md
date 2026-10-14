# 知识库检索 MCP + DeepSeek Agent

固定 300 篇 Markdown，提供 `kb_search`、`kb_fetch` 两个 MCP 工具；对比 F1/F2 扁平基线与 P1/P2/P3 父子检索。最小 Agent 使用 DeepSeek 发起工具调用，通过真实 MCP 获取原文，再组织有来源的回答。

任务范围见 [PLAN.md](PLAN.md)，执行证据见 [reports/validation.md](reports/validation.md)。M2 是后续可选扩展。本机已有模型和索引；Git 克隆不含这些生成资源。

## 接收方最快运行方式（Windows）

安装并启动 Docker Desktop，选择 Linux 容器模式。从 [Release v1.0.0](https://github.com/ZongshunXu/kb-mcp-project-1/releases/tag/v1.0.0) 下载 **全部 10 个附件** 到同一个新文件夹，包括五个模型分卷。打开该文件夹，在资源管理器地址栏输入 `powershell` 并回车，执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\initialize.ps1
```

初始化会导入镜像、解压完整模型和项目，直接检查随包 CPU 索引，不需要安装 Python 依赖或重新 Embedding。完成后复现五组实验：

```powershell
Set-Location .\new_project
docker compose run --rm --pull never kb eval --output reports/docker_eval --repeats 3 --warmup 1
```

结果在 `reports/docker_eval/summary.md`。详细说明见下载的 `RECEIVER.md`；检索评测不需要 DeepSeek Key，Agent 问答需要自己的 Key。公开仓库可以直接查看和下载，无需邀请。下面继续说明从源码自行构建的操作。

## Docker 启动与交接

接收者可以只安装 Docker，Python 和项目依赖统一安装在 Linux amd64 镜像中。Windows 使用 Docker Desktop 并选择 Linux 容器；Linux 使用 Docker Engine 和 Docker Compose。下面的命令都在包含 `compose.yaml` 的项目目录执行。

2026-10-14 已实际构建 CPU 镜像：容器内 **132 项测试通过**，固定 MCP 示例与五组真实 MCP 检查通过；使用完整 BGE-M3 为 300 篇语料生成了 `READY` 索引，模型指纹与九个产物的校验均通过。五组完整评测已通过，5 组 × 50 题 × 3 次共 **750 条测量**；召回率与原生环境一致。DeepSeek 联网问答仍等待有效 API Key。本节后面的命令同时用于接收者自行构建和复现。

微信打包保留源码、Docker 配置、`data/` 和 `reports/`，可以排除 `models/`、`artifacts/`、缓存和密钥。接收者将完整 BGE-M3 放入 `models/bge-m3/`，该层直接包含模型配置、权重和 Tokenizer 文件。模型独立于镜像，以只读目录挂载；首次下载模型和构建镜像仍需要时间，之后复用本地模型、镜像和索引。

```powershell
# 首次构建包含 Python 3.12 和 CPU 版依赖的镜像
docker compose build

# 先验证最小 MCP 协议；此示例不需要模型和索引
docker compose run --rm kb demo --check

# 源码交付首次生成索引；完整离线包若包含匹配的 CPU 索引可直接 check
docker compose run --rm kb index

# 检查五组真实 MCP
docker compose run --rm kb check

# 复现 M0 基线与 M1 对照；不需要 DeepSeek Key
docker compose run --rm kb eval --output reports/docker_eval --repeats 3 --warmup 1
```

评测完成后直接在电脑上打开 `reports/docker_eval/summary.md`。容器索引保存在 `artifacts/docker_cpu/`，报告和轨迹分别保存在 `reports/`、`logs/`，退出并删除临时容器后仍保留。Docker 与原生 Python 的索引分开保留；源码交付首次使用容器先运行 `index`，当前 Windows 索引不能直接作为容器索引复用。完整离线项目 ZIP 已包含本次生成并校验的 CPU 索引，并通过新接收目录的冷启动与重复初始化检查，无需接收者再次 Embedding。索引不会在启动时自动重建；语料、模型、分块规则或依赖版本变化后重新运行 `index`。

镜像使用固定 digest 的官方 Python 3.12.15 基础镜像和 CPU 版 PyTorch 2.7.1，MCP 保持 1.18.0；`requirements.txt` 另固定 `pydantic==2.11.7` 与 `pydantic-settings==2.10.1`，避免全新安装解析到不兼容版本。其余直接依赖保持原有锁定版本，镜像构建包含依赖一致性与 CPU 检查。

Compose 要求 `models/bge-m3/` 挂载目录先存在，固定示例只是不读取其中的模型。暂未准备模型目录时，构建镜像后可单独运行固定示例：

```powershell
docker run --rm kb-mcp:cpu demo --check
```

需要 DeepSeek 问答时，在同一个 PowerShell 终端隐藏输入 Key，再启动 Agent：

```powershell
$deepSeekSecret = Read-Host 'DeepSeek API Key' -AsSecureString
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', $deepSeekSecret).Password
docker compose run --rm kb agent --check-config
docker compose run --rm kb agent --group P1
```

每次出现问题提示后输入问题，输入 `exit` 或 `quit` 退出。也可复制 `.env.example` 为 `.env` 并填入自己的 Key，Compose 在运行时读取；`.env` 已排除在 Git 和镜像构建之外，交接时不要打包实际 Key。原生 Python 命令不自动读取 `.env`，其配置方式见后文。

`kb` 是一次执行一个命令的服务，入口为 `python run.py`，使用 `docker compose run --rm kb ...`。MCP 由客户端自动通过 stdio 启动，无需开放端口或另起常驻 Server。接收者后续运行只需进入项目目录再执行相应命令。Compose 配置方式见 [Docker 官方文档](https://docs.docker.com/reference/compose-file/services/)。

如果接收者希望省去首次构建，发送者可以在构建成功后另行导出镜像：

```powershell
docker save -o kb-mcp-cpu.tar kb-mcp:cpu
```

接收者收到镜像后导入，跳过上面的 `docker compose build`，其余操作相同：

```powershell
docker load -i kb-mcp-cpu.tar
```

镜像包包含 Python 依赖，体积大于纯源码包；接收者仍需要项目文件夹和 BGE-M3 模型。源码交接适合小包发送，预构建镜像适合提前准备好运行环境。导出和导入说明见 Docker 官方 [save](https://docs.docker.com/reference/cli/docker/image/save/) / [load](https://docs.docker.com/reference/cli/docker/image/load/) 文档。

## 原生 Python 运行准备

在本文件所在的仓库根目录操作，文件夹名称和磁盘位置不限。使用自己的 Python 3.12 环境：

```powershell
python -m pip install -r requirements.txt
$env:PYTHONIOENCODING = 'utf-8'
python run.py --help
```

原开发机器沿用已有 `lang_env`，MCP 保持 1.18.0，没有升级或新建环境。同事可以使用自己的兼容环境；PyCharm 的解释器与终端应使用同一个环境。

将完整本地 Hugging Face 格式 BGE-M3 放到 `models/bge-m3/`，包含配置、权重、Tokenizer 等完整文件。安装 Transformers/PyTorch 不等于取得模型权重。程序仅加载本地模型，不自动下载；可通过 `KB_MODEL_PATH` 指定位置。

```powershell
# 新克隆首次构建；已有匹配索引时无需重复
python run.py index
```

构建成功显示 `READY`。入库保存两套向量索引及原文、偏移和父子关系；日常查询只编码问题。语料、模型或分块规则变化后才重建。

## 使用

```powershell
# M0 固定样例：只验证协议，不加载模型或计算正式 Recall
python run.py demo --check

# 真实检索：自动启动 MCP，显示命中片段与 Fetch 原文
python run.py query --group P1 --query "User Service-1 中，离职员工权限回收有哪些业务规则和例外边界？"

# 检查五组真实 MCP
python run.py check

# 正式题集评测，另存复测结果
python run.py eval --output reports/local_eval

# 自动测试；使用独立 fixture，不要求模型或预先存在的索引
python run.py test -q
```

`run.py` 无参数只显示帮助，不自动重建。其余参数透传给对应模块，原有 `python -m scripts.index`、`scripts.query`、`scripts.check_mcp`、`scripts.eval` 仍可使用。保留经理要求的 `make index`、`make eval`；Windows 没有 GNU Make 时使用上述 Python 命令。

## DeepSeek 最小 Agent

BGE-M3 编码文档和问题，DeepSeek 负责工具选择及回答。两者是不同的模型。

配置 API Key 后运行。PowerShell 可用隐藏输入设置当前终端环境：

```powershell
$deepSeekSecret = Read-Host 'DeepSeek API Key' -AsSecureString
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', $deepSeekSecret).Password
python run.py agent --check-config
python run.py agent --group P1 --query "User Service-1 中，离职员工权限回收有哪些业务规则和例外边界？"
```

无 `--query` 时进入终端问答；每个问题独立检索，不保留跨问题会话记忆，输入 `exit` 或 `quit` 退出。只检索原文、不生成回答时使用 `run.py query`。

| 环境变量 | 默认值 / 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 必填，仅在运行环境中提供 |
| `DEEPSEEK_MODEL` | `deepseek-flash`，可覆盖 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |
| `DEEPSEEK_TIMEOUT` | 每个 HTTP 请求 60 秒 |
| `DEEPSEEK_MAX_TOKENS` | 每次响应最多 4096 Token |
| `KB_MODEL_PATH` | 可选，本地 BGE-M3 路径 |

可用 `--trace`（或 `--trace logs/my-run.json`）保存调用轨迹，`--max-turns`、`--max-tool-calls`、`--model-timeout`、`--mcp-timeout` 调整限额和等待时间。

Agent 从 MCP 获取两个工具的 schema，由 DeepSeek 提出工具调用；客户端执行并回传 `tool_call_id` 和结果。保留有限调用轮次、超时及错误退出，回答来源单独列出。Agent 不直接导入检索实现。正式 Recall 评测仍使用固定问题直接调用 MCP，独立于模型生成。

缺 Key 时 Agent 明确显示 `BLOCKED`，不发送请求，也不启动昂贵的检索模型。密钥不写入仓库、配置摘要或调用日志；检索 Server 不继承 DeepSeek Key。成功运行仅在 `--trace` 时保存轨迹；运行失败自动保存脱敏部分轨迹到 `logs/`（Git 忽略）。缺 Key 的配置预检在对话开始前退出，不生成对话轨迹。

默认使用非思考模式及非流式 Chat Completions。官方模型名称和请求格式依据 [DeepSeek 官方文档](https://api-docs.deepseek.com/quick_start) 与 [Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)。具体额度、网络和模型可用性以提供 Key 后的真实请求为准。

## 五组与工具契约

| 组 | 检索块 size/overlap | Fetch 原文 |
| --- | --- | --- |
| F1 | 240/50 | 当前块 |
| F2 | 960/200 | 当前块 |
| P1 | 240/50 | 960/200 父块 |
| P2 | 240/50 | 960/300 父块 |
| P3 | 240/50 | 1200/300 父块 |

单位为 Python 字符。F1/P1/P2/P3 共用小块、向量与索引；F2/P1 共用相同大块原文。P2/P3 父块只存文本和关系。所有组沿用相同 BGE-M3 Dense、CLS、L2 归一化及 FAISS IndexFlatIP。

- `kb_search(query, k=5, scope=None)` 返回 `node_id,parent_id,uri,text,score`。`scope` 保留在 schema 中，暂时忽略，不用于切换组。
- `kb_fetch(node_id)` 接收 Search 返回的检索 ID。P 组由 Server 映射父块；即使 Search 给出 `parent_id`，调用方仍传检索 ID。
- Fetch 响应的 `node_id` 是实际返回原文的节点。`neighbors` 固定为 parent/children/siblings，值仅为节点 ID 或 ID 列表。
- M2 未来可改变内部原文映射，但保留工具和参数契约；本期不实施 M2。
- 多父块包含时采用稳定映射，逐块验证完整包含。模式通过运行配置选择，Server 始终只暴露两个工具。

## 题集、报告与结果

正式输入为 `data/qrels/test.jsonl`、`data/qrels/review.json`。现有 50 题经助手核对原文并获用户批次批准；不是独立人工双标。保留 [审核定稿](data/qrels/审核定稿.md) 和 [修订依据](reports/annotation_review.md)，不修改题集或撤回原批准。

题集格式为每行一个 `query_id/query/evidence` 对象；证据含相对于语料目录的 `uri/start/end`。统一 UTF-8、CRLF/CR→LF、保留 BOM，区间为规范化文本的 `[start,end)`。修改题集或语料字节后须重新核对审核绑定；语料变化还需重建索引。

| 文件 | 用途 |
| --- | --- |
| [reports/summary.md](reports/summary.md) | 五组结果、增量、成本和限制 |
| [reports/results.csv](reports/results.csv) | 五组汇总 |
| [reports/per_query.csv](reports/per_query.csv) | 逐题命中、证据和测量明细 |
| [reports/mcp_check.json](reports/mcp_check.json) | 真实 MCP 功能检查 |
| [reports/validation.md](reports/validation.md) | 执行记录、清理说明、未验收事项 |

Recall 要求完整覆盖证据，同文档重叠/相邻区间先合并，每题计算后宏平均。Tool Token 计实际工具返回封装，另计去重 Fetch 原文 Token，使用固定 BGE-M3 Tokenizer；不是 DeepSeek 账单。默认每组每题预热一次、测量三次；重复调用不增加独立问题数。固定 TopK=5 不等于固定 Token 预算。

本次 Docker 与原生环境的全部 750 条测量中，Search/Fetch 命中 ID、区间和证据覆盖一致，召回率及去重原文 Token 相同。CPU 浮点评分的小数位略有变化，会影响包含 `score` 的完整工具 JSON 的 Token 数；本次组均值最大差为 0.32 Token/题，因此跨环境不要求完整封装 Token 逐字相同。耗时也会随机器与负载变化，复现详情见验收记录。

已有报告来自原开发机器及其 Docker 容器中的合成语料实验，保留真实结果，不代表其他机器或 DeepSeek 已通过验收。没有有效模型、索引或审核题集时评测显示 `BLOCKED`，未测字段留空。导师 Review 与程序检查、用户题集批准分开记录。

## GitHub 公开仓库交付

项目已发布到 [ZongshunXu/kb-mcp-project-1 公开仓库](https://github.com/ZongshunXu/kb-mcp-project-1)。同事可以直接查看源码；完整运行包在 [Release v1.0.0](https://github.com/ZongshunXu/kb-mcp-project-1/releases/tag/v1.0.0)，共 10 个附件。

| Release 附件 | 内容 |
| --- | --- |
| `kb-mcp-project.zip` | 项目、300 篇语料、题集、报告及已验收的 Docker CPU 索引 |
| `kb-mcp-cpu.tar.gz` | 预构建 Linux amd64 CPU 镜像，包含 Python 与依赖 |
| `bge-m3-model.zip.part001` 至 `part005` | 完整 BGE-M3 模型的五个分卷，必须全部下载 |
| `manifest.json` | 大小与 SHA-256 校验清单 |
| `initialize.ps1` | Windows 初始化入口 |
| `RECEIVER.md` | 中文接收方指南与 Key 配置说明 |

下载全部附件到同一个新文件夹，按照本文件开头或 `RECEIVER.md` 的步骤运行。文件已在本机全新接收目录通过初始化验收；十个远程附件的大小与 SHA-256 均与本地一致。项目 ZIP 中的文档保留打包时状态，仓库和 Release 页面记录当前发布状态。

Git 保留源码、配置、依赖、300 篇语料、题集和报告。模型、索引、实际 `.env`、日志和交付包排除在 Git 之外。普通 Git 单文件限制为 **100 MiB**，Release 单文件必须小于 **2 GiB**，因此完整模型按 512 MiB 分卷。依据 [GitHub 大文件说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github) 与 [Release 附件说明](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)。

GitHub 自动生成的 **Source code (zip)** 只含源码，不包含独立上传的镜像、模型、索引或初始化入口；仅使用源码时需自行准备模型、构建镜像和生成索引。完整离线交付文件在开发机器的 `artifacts/handoff/`，通过 Release 下载取得。

## Git 与最简目录

```text
README.md / PLAN.md       运行交接 / 已确认任务范围
run.py / Makefile         统一入口及构建评测命令
Dockerfile / compose.yaml 容器镜像与一次性运行服务
.dockerignore            排除模型、生成资源及密钥
.env.example             Docker 运行环境配置示例
agent/                    DeepSeek 请求与模型工具调用循环
kb/                       分块、索引、检索、两工具 Server
scripts/                  构建、查询、评测、Agent CLI 与 M0 示例
tests/                    必要单元与协议/Agent 联调测试
config/                   五组参数
data/corpus/              300 篇固定语料
data/qrels/               正式题集、审核记录及可读依据
models/                   本地 BGE-M3，Git 忽略
artifacts/                本地索引与关系，Git 忽略
artifacts/docker_cpu/     独立的 Docker CPU 索引，Git 忽略
reports/                  正式实验及验收记录
logs/                     按需生成的本机 Agent 轨迹，Git 忽略
```

提交源码、配置、依赖、语料、正式题集与报告。模型单独交接；向量索引默认不提交，同事准备相同模型后首次重建。`.gitattributes` 保留语料与 qrels JSONL 的原始字节，防止 Git 行尾转换破坏审核哈希。

不提交 IDE 配置、虚拟环境、缓存、密钥或试跑目录。旧讨论稿、重复上手说明与临时产物已合并清理，正式输入和实验依据保留。
