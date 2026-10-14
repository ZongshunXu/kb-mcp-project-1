# 五组知识库检索评测

状态：OK；完成 5/5 组。

| 组 | Search Recall@5 | Fetch Recall@5 | Tool Token/题 | 去重上下文 Token/题 | 检索块 | 父块 | FAISS MB | 元数据 MB | 合计 MB | Search ms | Fetch ms | 合计 ms | 状态 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| F1 | 0.8900 | 0.8900 | 5984.5800 | 703.0600 | 2070 | 0 | 8.4788 | 1.3627 | 9.8415 | 74.1623 | 10.1219 | 84.3001 | OK |
| F2 | 1.0000 | 1.0000 | 15214.0400 | 2609.4200 | 600 | 0 | 2.4576 | 1.1222 | 3.5799 | 71.6759 | 10.3759 | 82.0672 | OK |
| P1 | 0.8900 | 1.0000 | 7963.1400 | 1395.4800 | 2070 | 600 | 8.4788 | 2.6661 | 11.1449 | 73.8253 | 6.2282 | 80.0657 | OK |
| P2 | 0.8900 | 1.0000 | 8107.9800 | 1412.5200 | 2070 | 600 | 8.4788 | 2.7331 | 11.2118 | 73.7339 | 6.2101 | 79.9555 | OK |
| P3 | 0.8900 | 1.0000 | 8031.8400 | 1577.6000 | 2070 | 570 | 8.4788 | 2.7329 | 11.2117 | 73.8468 | 4.7996 | 78.6569 | OK |

## 对照与成本

- 主要对照 Search（P1 相对 F2）：-11.00 个百分点；相对增益 -11.00%。
- 主要对照 Fetch（P1 相对 F2）：+0.00 个百分点；相对增益 +0.00%。
- 相同子块检索的 Fetch 上下文增益（P1 相对 F1）：+11.00 个百分点；相对增益 +12.36%。
- 扁平大小块 Fetch（F2 相对 F1）：+11.00 个百分点；相对增益 +12.36%。
- 父块 overlap 200→300（P2 相对 P1）：+0.00 个百分点；相对增益 +0.00%。
- 父块 size 960→1200（P3 相对 P2）：+0.00 个百分点；相对增益 +0.00%。

P1/P2/P3 子块均为 240/50；父块依次为 960/200、960/300、1200/300。参数扩大可能增加覆盖，也增加上下文与元数据成本，结论以实测表格为准。
F1 与 P1/P2/P3 的检索索引应共享；Search Recall@5 应一致。F2 与 P1 的大块范围应一致。
共享检索块 Search Recall 检查：一致。
P1 相对 F2 每题 Tool Token 差 -7250.90，去重上下文 Token 差 -1213.94，客户端合计延迟差 -2.00 ms。
P2 相对 P1 每题 Tool Token 差 +144.84，去重上下文 Token 差 +17.04，客户端合计延迟差 -0.11 ms。
P3 相对 P1 每题 Tool Token 差 +68.70，去重上下文 Token 差 +182.12，客户端合计延迟差 -1.41 ms。

## 失败案例

- F1 / draft007：Fetch Recall=0.5000；未覆盖证据 `[{"end": 1041, "fetch_covered": false, "search_covered": false, "start": 1000, "uri": "enterprise_doc_037.md"}]`。
- F1 / draft014：Fetch Recall=0.5000；未覆盖证据 `[{"end": 1027, "fetch_covered": false, "search_covered": false, "start": 979, "uri": "enterprise_doc_079.md"}]`。
- F1 / draft015：Fetch Recall=0.0000；未覆盖证据 `[{"end": 388, "fetch_covered": false, "search_covered": false, "start": 366, "uri": "enterprise_doc_085.md"}, {"end": 422, "fetch_covered": false, "search_covered": false, "start": 403, "uri": "enterprise_doc_085.md"}]`。
- F1 / draft017：Fetch Recall=0.0000；未覆盖证据 `[{"end": 359, "fetch_covered": false, "search_covered": false, "start": 308, "uri": "enterprise_doc_097.md"}, {"end": 423, "fetch_covered": false, "search_covered": false, "start": 374, "uri": "enterprise_doc_097.md"}]`。
- F1 / draft018：Fetch Recall=0.5000；未覆盖证据 `[{"end": 1055, "fetch_covered": false, "search_covered": false, "start": 1009, "uri": "enterprise_doc_103.md"}]`。
- F1 / draft028：Fetch Recall=0.5000；未覆盖证据 `[{"end": 371, "fetch_covered": false, "search_covered": false, "start": 325, "uri": "enterprise_doc_163.md"}]`。
- F1 / draft032：Fetch Recall=0.5000；未覆盖证据 `[{"end": 1057, "fetch_covered": false, "search_covered": false, "start": 1008, "uri": "enterprise_doc_187.md"}]`。
- F1 / draft039：Fetch Recall=0.5000；未覆盖证据 `[{"end": 1015, "fetch_covered": false, "search_covered": false, "start": 972, "uri": "enterprise_doc_229.md"}]`。
- F1 / draft047：Fetch Recall=0.5000；未覆盖证据 `[{"end": 628, "fetch_covered": false, "search_covered": false, "start": 577, "uri": "enterprise_doc_277.md"}]`。
完整问题、所有重复的命中区间与证据判断保存在 per_query.csv。

## 计算口径与局限

- 同一经审核并批准的标注集：50 题；每组每题预热 1 次，测量 3 次。预热与进程启动/initialize 不计延迟。
- [start,end) 采用 Python 字符偏移；相同 URI 的重叠/相邻返回区间先求并集。每条证据仅在完整覆盖时命中，先算每题比例，再对问题宏平均。
- 同一父块仅 Fetch 一次，实际传入第一个命中子块的 node_id。avg_fetch_ms 是每题所有实际 Fetch 调用耗时之和。avg_total_ms 从 Search 发起到最后一个 Fetch 返回，包含必要的 Search 解析与客户端分派；Fetch 解析、元数据核验、证据计分和 tokenization 排除在计时之外。
- Tool Token 计数范围：实际每个 MCP CallToolResult.model_dump_json() 解析后按 sort_keys=True、ensure_ascii=False、紧凑 JSON 重新序列化的全部输出封装，保留 TextContent 与 structuredContent 的重复内容；不计请求、传输帧或初始化。
- 去重上下文 Token 只对 Fetch 原文的 URI 区间并集逐段编码后相加；不加入合成分隔符，不加 special tokens。两种 Token 不应混用。
- 固定 tokenizer：BAAI/bge-m3；transformers 版本 4.54.1；文件指纹 78abcc09f58b8418a7a247ab7182c4d60f757c2c5e74e562bd64d7720957bb10。
- 语料文件数 300；语料 digest `205c9691c721e26e3689f33be7f4ae84db49dece58f254edeccebb9a75ba2faf`。当前语料自述为合成实验语料，不能据此推广到真实企业知识库。
- 文件大小使用 MB（字节 / 1,000,000）；共享物理索引在每组按逻辑依赖计成本，各组相加不表示磁盘实际占用。
- 样本有限，重复调用用于本机均值估计；这是基础实验，不能视为生产负载测试。未测字段在 CSV 留空，表格显示 —。
- 不设置强制召回提升阈值。未提升、回退或基线为 0 的结果均如实保留；零基线相对增益为 N/A，仍报告绝对百分点差。

- 各组按 F1/F2/P1/P2/P3 顺序运行，CPU 频率、缓存和后台负载可能影响延迟；父块参数不会改变相同子块索引的 query embedding，不应把组间 Search 延迟差直接解释为父子结构的提速。

## 标注来源与确认方式

- 审核记录：本会话用户（确认全部通过）；Codex 辅助核对原文；时间 2026-10-13T21:04:36+08:00。
- 审核方式：assistant source audit plus explicit user conversation approval。
- 记录说明：用户授权助手审核并给修改意见，随后确认本批50题全部通过。助手核对50篇全文，修正30题因固定章节号导致的证据定位错配，问题保持不变。未声称用户逐题亲自阅读。
- qrels SHA256：`06aaafb83e7ae4a3ca03a1c6807417dca70ba0f7011af477f0f8779249d53cc3`。
- 本批问题的抽样及题型限制见 data/qrels/审核定稿.md 和 reports/annotation_review.md；指标是证据区间覆盖召回，不是回答正确率。
