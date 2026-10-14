"""Evaluate the five groups through real MCP stdio calls and reviewed qrels."""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from kb.metrics import (
    compare_recall, coverage_details, dedup_context_tokens, macro_average,
    merge_intervals, tool_token_count, validate_interval,
)
from scripts.mcp_client import EXPERIMENT_IDS, ROOT, connect, normalize_result

RESULT_FIELDS = (
    "group", "search_recall_at_5", "fetch_recall_at_5", "avg_tool_tokens",
    "dedup_context_tokens", "search_chunks", "parent_chunks", "faiss_mb",
    "metadata_mb", "total_index_mb", "avg_search_ms", "avg_fetch_ms", "avg_total_ms",
    "query_count", "repeats", "measured_samples", "warmup", "status", "error",
)
QUERY_FIELDS = (
    "group", "query_id", "query", "repeat", "search_recall_at_5", "fetch_recall_at_5",
    "tool_tokens", "dedup_context_tokens", "search_ms", "fetch_ms", "total_ms",
    "fetch_calls", "search_node_ids_json", "search_scores_json", "fetch_node_ids_json", "search_intervals_json",
    "fetch_intervals_json", "evidence_json",
)
COST_FIELDS = ("search_chunks", "parent_chunks", "faiss_mb", "metadata_mb", "total_index_mb")


class EvaluationBlocked(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def project_path(path: str | Path, root: Path = ROOT) -> Path:
    path = Path(path)
    resolved = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise EvaluationBlocked(f"索引/元数据路径越出项目根目录: {path}")
    return resolved


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_corpus(root: Path) -> tuple[dict, dict[str, str]]:
    """Use the build's fixed corpus digest and text policy, without retrieving."""
    from kb.chunker import read_text
    from kb.indexer import corpus_manifest

    corpus = corpus_manifest(root)
    texts = {item["uri"]: read_text(project_path(Path("data/corpus") / item["uri"], root))
             for item in corpus["files"]}
    return corpus, texts


def load_reviewed_qrels(
    qrels_path: Path, review_path: Path, corpus: dict, texts: dict[str, str],
) -> list[dict]:
    if not qrels_path.is_file() or not review_path.is_file():
        raise EvaluationBlocked(
            "缺少人工审核标注：需要 data/qrels/test.jsonl 和绑定该文件的 review.json；"
            "example 文件不能作为正式金标准"
        )
    try:
        review = read_json(review_path)
    except (ValueError, OSError) as exc:
        raise EvaluationBlocked(f"人工审核记录读取失败: {exc}") from exc
    if not isinstance(review, dict) or review.get("status") != "approved":
        raise EvaluationBlocked("review.json 的 status 必须为人工填写的 approved")
    if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        raise EvaluationBlocked("review.json 缺少人工审核人 reviewer")
    try:
        reviewed_at = datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
        if reviewed_at.tzinfo is None:
            raise ValueError("审核时间须含时区")
    except (KeyError, ValueError, AttributeError, TypeError) as exc:
        raise EvaluationBlocked("reviewed_at 必须为带时区的 ISO 8601 人工审核时间") from exc
    if review.get("qrels_sha256") != sha256_file(qrels_path):
        raise EvaluationBlocked("qrels_sha256 与正式 test.jsonl 不一致，请重新人工审核并绑定")
    if review.get("corpus_digest") != corpus["digest"]:
        raise EvaluationBlocked("review.corpus_digest 与当前语料不一致，请重新人工审核")
    qrels, ids = [], set()
    try:
        for line_no, line in enumerate(qrels_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            item = json.loads(line)
            if item.get("annotation_status") == "example_only_not_human_reviewed":
                raise ValueError(f"第 {line_no} 行仍标记为未人工审核的格式示例")
            query_id, query, evidence = item.get("query_id"), item.get("query"), item.get("evidence")
            if not isinstance(query_id, str) or not query_id.strip() or query_id in ids:
                raise ValueError(f"第 {line_no} 行 query_id 为空或重复")
            if not isinstance(query, str) or not query.strip():
                raise ValueError(f"第 {line_no} 行 query 为空")
            if not isinstance(evidence, list) or not evidence:
                raise ValueError(f"第 {line_no} 行 evidence 须至少有一个原文区间")
            seen_evidence = set()
            for interval in evidence:
                uri, start, end = validate_interval(interval)
                if uri not in texts or end > len(texts[uri]):
                    raise ValueError(f"第 {line_no} 行证据不存在或越界: {interval}")
                if (uri, start, end) in seen_evidence:
                    raise ValueError(f"第 {line_no} 行包含重复证据区间")
                seen_evidence.add((uri, start, end))
            ids.add(query_id)
            qrels.append(item)
    except (ValueError, OSError, AttributeError, TypeError) as exc:
        raise EvaluationBlocked(f"正式标注校验失败: {exc}") from exc
    if not qrels:
        raise EvaluationBlocked("正式标注集为空")
    return qrels


def load_nodes(path: Path, texts: dict[str, str]) -> dict[str, dict]:
    nodes = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        node = json.loads(line)
        uri, start, end = validate_interval(node)
        node_id = node.get("node_id")
        if not isinstance(node_id, str) or not node_id or node_id in nodes:
            raise ValueError(f"节点 ID 缺失或重复: {node_id}")
        if uri not in texts or end > len(texts[uri]) or node.get("text") != texts[uri][start:end]:
            raise ValueError(f"节点与原始语料区间不符: {node_id}")
        nodes[node_id] = node
    return nodes


def prepare_group(group: str, manifest: dict, texts: dict[str, str], root: Path) -> dict:
    metadata = manifest["experiments"][group]
    for key in ("search_nodes", "faiss"):
        if not metadata.get(key) or not project_path(metadata[key], root).is_file():
            raise EvaluationBlocked(f"{group} 缺少索引文件 {key}；先执行 python -m scripts.index")
    search = load_nodes(project_path(metadata["search_nodes"], root), texts)
    parents, relations = {}, {}
    if group.startswith("P"):
        for key in ("parent_nodes", "relations"):
            if not metadata.get(key) or not project_path(metadata[key], root).is_file():
                raise EvaluationBlocked(f"{group} 缺少父子元数据 {key}")
        parents = load_nodes(project_path(metadata["parent_nodes"], root), texts)
        relations = read_json(project_path(metadata["relations"], root))
        if not isinstance(relations, dict) or set(relations) != set(search):
            raise ValueError(f"{group} 父子关系不完整")
        for child_id, parent_id in relations.items():
            child, parent = search[child_id], parents[parent_id]
            if child["uri"] != parent["uri"] or not (
                parent["start"] <= child["start"] < child["end"] <= parent["end"]
            ):
                raise ValueError(f"{group} 父块未完整包含子块 {child_id}")
    if len(search) != metadata.get("search_chunks") or len(parents) != metadata.get("parent_chunks"):
        raise ValueError(f"{group} 节点数量与 manifest 不符")
    return {"search": search, "parents": parents, "relations": relations}


def load_fixed_tokenizer(manifest: dict, root: Path) -> tuple[Any, dict]:
    from kb.embedding import load_tokenizer

    metadata = manifest.get("tokenizer", {})
    expected_version = metadata.get("version")
    actual_version = importlib.metadata.version("transformers")
    if not expected_version or expected_version != actual_version:
        raise EvaluationBlocked(
            f"Tokenizer 版本未固定或与索引不符: manifest={expected_version}, 当前={actual_version}"
        )
    if not metadata.get("fingerprint"):
        raise EvaluationBlocked("manifest 缺少 tokenizer 文件指纹；请用同一模型重新构建索引")
    model_path = os.environ.get("KB_MODEL_PATH") or metadata.get("path") or metadata.get("model_path")
    model_path = model_path or manifest.get("embedding", {}).get("model_path")
    if not model_path:
        raise EvaluationBlocked("找不到本地 BGE-M3 tokenizer/model；请设置 KB_MODEL_PATH")
    candidate = Path(model_path)
    if not candidate.is_absolute() and (root / candidate).exists():
        model_path = str((root / candidate).resolve())
    tokenizer = load_tokenizer(model_path, expected_fingerprint=metadata["fingerprint"])
    return tokenizer, metadata


def interval_for(node: dict) -> dict:
    return {key: node[key] for key in ("uri", "start", "end")}


def check_search(payload: Any, nodes: dict, group: str) -> list[dict]:
    if not isinstance(payload, list) or len(payload) > 5:
        raise ValueError(f"{group}: kb_search 必须返回不超过 5 项的列表")
    previous_score, previous_id = float("inf"), ""
    ids = set()
    for hit in payload:
        node_id = hit.get("node_id")
        if node_id not in nodes["search"] or node_id in ids:
            raise ValueError(f"{group}: kb_search 返回未知或重复节点 {node_id}")
        node = nodes["search"][node_id]
        if hit.get("uri") != node["uri"] or hit.get("text") != node["text"]:
            raise ValueError(f"{group}: kb_search 返回文本与记录偏移不一致: {node_id}")
        parent_id = nodes["relations"].get(node_id)
        if "parent_id" not in hit or hit["parent_id"] != parent_id:
            raise ValueError(f"{group}: kb_search parent_id 与关系元数据不符")
        score = hit.get("score")
        if isinstance(score, bool) or not isinstance(score, (float, int)) or not math.isfinite(score):
            raise ValueError(f"{group}: kb_search score 必须为有限数值")
        if score > previous_score or (score == previous_score and node_id < previous_id):
            raise ValueError(f"{group}: 检索排序不符合 score 降序/node_id 同分升序")
        previous_score, previous_id = score, node_id
        ids.add(node_id)
    return payload


def check_fetch(payload: Any, hit: dict, nodes: dict, group: str) -> dict:
    if not isinstance(payload, dict):
        raise ValueError(f"{group}: kb_fetch 必须返回对象")
    expected_id = hit.get("parent_id") or hit["node_id"]
    expected_node = (nodes["parents"] if hit.get("parent_id") else nodes["search"])[expected_id]
    for key in ("node_id", "uri", "text"):
        if payload.get(key) != expected_node[key]:
            raise ValueError(f"{group}: kb_fetch {key} 与预期原文节点不符")
    neighbors = payload.get("neighbors")
    if not isinstance(neighbors, dict) or set(neighbors) != {"parent", "children", "siblings"}:
        raise ValueError(f"{group}: neighbors 契约不符")
    known_ids = set(nodes["search"]) | set(nodes["parents"])
    parent = neighbors["parent"]
    if parent is not None and (not isinstance(parent, str) or parent not in known_ids):
        raise ValueError(f"{group}: neighbors.parent 不是有效 node_id")
    for key in ("children", "siblings"):
        if not isinstance(neighbors[key], list) or any(
            not isinstance(ref, str) or ref not in known_ids for ref in neighbors[key]
        ):
            raise ValueError(f"{group}: neighbors.{key} 含非法节点引用")
    return expected_node


async def timed_call(session: Any, name: str, arguments: dict) -> tuple[Any, float]:
    started = time.perf_counter()
    result = await session.call_tool(name, arguments)
    elapsed_ms = (time.perf_counter() - started) * 1000
    # Parsing, validation, tokenization and scoring happen outside the clock.
    return result, elapsed_ms


async def query_once(
    session: Any, query: dict, group: str, nodes: dict, texts: dict[str, str], tokenizer: Any,
    repeat: int, count_metrics: bool = True,
) -> dict:
    pipeline_started = time.perf_counter()
    search_result, search_ms = await timed_call(session, "kb_search", {"query": query["query"], "k": 5})
    hits = normalize_result(search_result, expected_type=list)
    if not isinstance(hits, list):
        raise ValueError(f"{group}: kb_search 必须返回列表")
    fetch_pairs, fetched, fetch_ms = [], set(), 0.0
    for hit in hits:
        context_id = hit.get("parent_id") or hit["node_id"]
        if context_id in fetched:
            continue
        # Always pass the original search child ID, even when deduping by parent ID.
        result, elapsed = await timed_call(session, "kb_fetch", {"node_id": hit["node_id"]})
        fetch_ms += elapsed
        fetched.add(context_id)
        fetch_pairs.append((hit, result))
    total_ms = (time.perf_counter() - pipeline_started) * 1000
    # Defer metadata validation, Fetch parsing, evidence scoring and tokens until
    # the complete MCP pipeline clock has stopped. Search parsing/dispatch is
    # needed to select IDs and therefore is included in total_ms.
    check_search(hits, nodes, group)
    search_intervals = [interval_for(nodes["search"][hit["node_id"]]) for hit in hits]
    fetch_nodes = [check_fetch(normalize_result(result), hit, nodes, group)
                   for hit, result in fetch_pairs]
    fetch_intervals = [interval_for(node) for node in fetch_nodes]
    fetch_ids = [node["node_id"] for node in fetch_nodes]
    fetch_results = [result for _, result in fetch_pairs]
    if not count_metrics:
        return {}
    search_details = coverage_details(query["evidence"], search_intervals)
    fetch_details = coverage_details(query["evidence"], fetch_intervals)
    evidence = [{**item, "search_covered": search_details[i]["covered"],
                 "fetch_covered": fetch_details[i]["covered"]}
                for i, item in enumerate(query["evidence"])]
    compact = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "group": group, "query_id": query["query_id"], "query": query["query"], "repeat": repeat,
        "search_recall_at_5": sum(item["covered"] for item in search_details) / len(search_details),
        "fetch_recall_at_5": sum(item["covered"] for item in fetch_details) / len(fetch_details),
        "tool_tokens": tool_token_count(search_result, tokenizer) +
                       sum(tool_token_count(result, tokenizer) for result in fetch_results),
        "dedup_context_tokens": dedup_context_tokens(fetch_intervals, texts, tokenizer),
        "search_ms": search_ms, "fetch_ms": fetch_ms, "total_ms": total_ms,
        "fetch_calls": len(fetch_results),
        "search_node_ids_json": compact([hit["node_id"] for hit in hits]),
        "search_scores_json": compact([hit["score"] for hit in hits]),
        "fetch_node_ids_json": compact(fetch_ids),
        "search_intervals_json": compact(merge_intervals(search_intervals)),
        "fetch_intervals_json": compact(merge_intervals(fetch_intervals)),
        "evidence_json": compact(evidence),
    }


async def evaluate_group(
    group: str, qrels: list[dict], nodes: dict, texts: dict[str, str], tokenizer: Any,
    repeats: int, warmup: int, root: Path,
) -> list[dict]:
    rows = []
    async with connect(group, root=root) as session:
        tools = (await session.list_tools()).tools
        if sorted(tool.name for tool in tools) != ["kb_fetch", "kb_search"]:
            raise ValueError(f"{group}: tools/list 必须只包含 kb_search 与 kb_fetch")
        search_schema = next(tool.inputSchema for tool in tools if tool.name == "kb_search")
        if not {"query", "k", "scope"}.issubset(search_schema.get("properties", {})):
            raise ValueError(f"{group}: kb_search schema 缺少 query/k/scope")
        for _ in range(warmup):
            for query in qrels:
                await query_once(session, query, group, nodes, texts, tokenizer, 0, count_metrics=False)
        canonical = {}
        for repeat in range(1, repeats + 1):
            for query in qrels:
                row = await query_once(session, query, group, nodes, texts, tokenizer, repeat)
                signature = tuple(row[key] for key in (
                    "search_node_ids_json", "fetch_node_ids_json", "evidence_json",
                ))
                query_id = query["query_id"]
                if query_id in canonical and canonical[query_id] != signature:
                    raise ValueError(f"{group}/{query_id}: 重复运行的节点或证据判断不一致")
                canonical[query_id] = signature
                rows.append(row)
    return rows


def blank_results(manifest: dict, reason: str = "") -> list[dict]:
    results = []
    for group in EXPERIMENT_IDS:
        row = dict.fromkeys(RESULT_FIELDS, "")
        row.update(group=group, status="BLOCKED", error=reason)
        metadata = manifest.get("experiments", {}).get(group, {})
        for key in COST_FIELDS:
            # Retain actual static costs only if the build recorded them.
            if key in metadata and metadata[key] is not None:
                row[key] = metadata[key]
        results.append(row)
    return results


def aggregate(row: dict, samples: list[dict], query_count: int, repeats: int, warmup: int) -> None:
    for output, source in (
        ("search_recall_at_5", "search_recall_at_5"),
        ("fetch_recall_at_5", "fetch_recall_at_5"), ("avg_tool_tokens", "tool_tokens"),
        ("dedup_context_tokens", "dedup_context_tokens"), ("avg_search_ms", "search_ms"),
        ("avg_fetch_ms", "fetch_ms"), ("avg_total_ms", "total_ms"),
    ):
        by_query = {}
        for sample in samples:
            by_query.setdefault(sample["query_id"], []).append(sample[source])
        row[output] = macro_average(statistics.mean(values) for values in by_query.values())
    row.update(query_count=query_count, repeats=repeats, measured_samples=len(samples),
               warmup=warmup, status="OK", error="")


def verify_shared_search(results: list[dict], per_query: list[dict]) -> list[str]:
    """A shared index must produce the same per-query hits, not merely a mean."""
    by_group = {row["group"]: row for row in results}
    if by_group["F1"]["status"] != "OK":
        return []
    samples = {(row["group"], row["query_id"], row["repeat"]): row for row in per_query}
    baseline_rows = [row for row in per_query if row["group"] == "F1"]
    errors = []
    for group in ("P1", "P2", "P3"):
        if by_group[group]["status"] != "OK":
            continue
        for baseline in baseline_rows:
            candidate = samples[(group, baseline["query_id"], baseline["repeat"])]
            same = all(candidate[key] == baseline[key] for key in (
                "search_node_ids_json", "search_intervals_json", "search_recall_at_5",
            ))
            baseline_scores = json.loads(baseline["search_scores_json"])
            candidate_scores = json.loads(candidate["search_scores_json"])
            same_scores = len(baseline_scores) == len(candidate_scores) and all(
                math.isclose(left, right, rel_tol=0, abs_tol=1e-6)
                for left, right in zip(baseline_scores, candidate_scores)
            )
            if not same or not same_scores:
                message = f"共享检索索引一致性失败: F1/{group}/{baseline['query_id']}/repeat={baseline['repeat']}"
                by_group[group].update(status="FAILED", error=message)
                errors.append(message)
                break
    return errors


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def display(value: Any) -> str:
    if value == "" or value is None:
        return "—"
    return f"{value:.4f}" if isinstance(value, float) else str(value)


def gain_line(results: dict, baseline: str, candidate: str, field: str, label: str) -> str:
    left, right = results[baseline].get(field), results[candidate].get(field)
    if not isinstance(left, (float, int)) or not isinstance(right, (float, int)):
        return f"- {label}（{candidate} 相对 {baseline}）：未测，N/A。"
    gain = compare_recall(left, right)
    relative = "N/A（基线为 0）" if gain["relative_percent"] is None else f"{gain['relative_percent']:+.2f}%"
    return f"- {label}（{candidate} 相对 {baseline}）：{gain['absolute_percentage_points']:+.2f} 个百分点；相对增益 {relative}。"


def write_summary(
    path: Path, results: list[dict], per_query: list[dict], manifest: dict,
    reasons: list[str], tokenizer_info: dict, qrels: list[dict], repeats: int, warmup: int,
) -> None:
    completed = sum(row["status"] == "OK" for row in results)
    lines = ["# 五组知识库检索评测", "",
             f"状态：{'OK' if completed == 5 and not reasons else 'BLOCKED'}；完成 {completed}/5 组。",
             ""]
    if reasons:
        lines.extend(["阻塞原因：", ""] + [f"- {reason}" for reason in reasons] + [""])
    lines.extend([
        "| 组 | Search Recall@5 | Fetch Recall@5 | Tool Token/题 | 去重上下文 Token/题 | 检索块 | 父块 | FAISS MB | 元数据 MB | 合计 MB | Search ms | Fetch ms | 合计 ms | 状态 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ])
    table_fields = RESULT_FIELDS[:13] + ("status",)
    for row in results:
        lines.append("| " + " | ".join(display(row.get(key, "")) for key in table_fields) + " |")
    indexed = {row["group"]: row for row in results}
    lines.extend(["", "## 对照与成本", "",
                  gain_line(indexed, "F2", "P1", "search_recall_at_5", "主要对照 Search"),
                  gain_line(indexed, "F2", "P1", "fetch_recall_at_5", "主要对照 Fetch"),
                  gain_line(indexed, "F1", "P1", "fetch_recall_at_5", "相同子块检索的 Fetch 上下文增益"),
                  gain_line(indexed, "F1", "F2", "fetch_recall_at_5", "扁平大小块 Fetch"),
                  gain_line(indexed, "P1", "P2", "fetch_recall_at_5", "父块 overlap 200→300"),
                  gain_line(indexed, "P2", "P3", "fetch_recall_at_5", "父块 size 960→1200"), "",
                  "P1/P2/P3 子块均为 240/50；父块依次为 960/200、960/300、1200/300。"
                  "参数扩大可能增加覆盖，也增加上下文与元数据成本，结论以实测表格为准。",
                  "F1 与 P1/P2/P3 的检索索引应共享；Search Recall@5 应一致。F2 与 P1 的大块范围应一致。"])
    shared_rows = [indexed[group] for group in ("F1", "P1", "P2", "P3")]
    if all(row["status"] == "OK" for row in shared_rows):
        recalls = [row["search_recall_at_5"] for row in shared_rows]
        lines.append("共享检索块 Search Recall 检查：" + ("一致。" if max(recalls) - min(recalls) < 1e-12 else "不一致，需核查索引/排序/调用记录。"))
    for candidate in ("P1", "P2", "P3"):
        left, right = indexed["F2" if candidate == "P1" else "P1"], indexed[candidate]
        if left["status"] == right["status"] == "OK":
            baseline = left["group"]
            lines.append(f"{candidate} 相对 {baseline} 每题 Tool Token 差 {right['avg_tool_tokens'] - left['avg_tool_tokens']:+.2f}，"
                         f"去重上下文 Token 差 {right['dedup_context_tokens'] - left['dedup_context_tokens']:+.2f}，"
                         f"客户端合计延迟差 {right['avg_total_ms'] - left['avg_total_ms']:+.2f} ms。")
    failures = [row for row in per_query if row["repeat"] == 1 and row["fetch_recall_at_5"] < 1]
    lines.extend(["", "## 失败案例", ""])
    if failures:
        for row in failures[:20]:
            uncovered = [item for item in json.loads(row["evidence_json"]) if not item["fetch_covered"]]
            lines.append(f"- {row['group']} / {row['query_id']}：Fetch Recall={row['fetch_recall_at_5']:.4f}；"
                         f"未覆盖证据 `{json.dumps(uncovered, ensure_ascii=False)}`。")
        lines.append("完整问题、所有重复的命中区间与证据判断保存在 per_query.csv。")
    else:
        lines.append("未进行正式测量，暂无可报告的失败案例。" if not per_query else "实测问题均完整覆盖；这不能证明其他问题也会覆盖。")
    lines.extend(["", "## 计算口径与局限", "",
                  f"- 同一经审核并批准的标注集：{len(qrels)} 题；每组每题预热 {warmup} 次，测量 {repeats} 次。预热与进程启动/initialize 不计延迟。",
                  "- [start,end) 采用 Python 字符偏移；相同 URI 的重叠/相邻返回区间先求并集。每条证据仅在完整覆盖时命中，先算每题比例，再对问题宏平均。",
                  "- 同一父块仅 Fetch 一次，实际传入第一个命中子块的 node_id。avg_fetch_ms 是每题所有实际 Fetch 调用耗时之和。avg_total_ms 从 Search 发起到最后一个 Fetch 返回，包含必要的 Search 解析与客户端分派；Fetch 解析、元数据核验、证据计分和 tokenization 排除在计时之外。",
                  "- Tool Token 计数范围：实际每个 MCP CallToolResult.model_dump_json() 解析后按 sort_keys=True、ensure_ascii=False、紧凑 JSON 重新序列化的全部输出封装，保留 TextContent 与 structuredContent 的重复内容；不计请求、传输帧或初始化。",
                  "- 去重上下文 Token 只对 Fetch 原文的 URI 区间并集逐段编码后相加；不加入合成分隔符，不加 special tokens。两种 Token 不应混用。",
                  f"- 固定 tokenizer：{tokenizer_info.get('name', '未加载')}；transformers 版本 {tokenizer_info.get('version', '未加载')}；文件指纹 {tokenizer_info.get('fingerprint', '未加载')}。",
                  f"- 语料文件数 {manifest.get('corpus', {}).get('count', '未知')}；语料 digest `{manifest.get('corpus', {}).get('digest', '未知')}`。当前语料自述为合成实验语料，不能据此推广到真实企业知识库。",
                  "- 文件大小使用 MB（字节 / 1,000,000）；共享物理索引在每组按逻辑依赖计成本，各组相加不表示磁盘实际占用。",
                  "- 样本有限，重复调用用于本机均值估计；这是基础实验，不能视为生产负载测试。未测字段在 CSV 留空，表格显示 —。",
                  "- 不设置强制召回提升阈值。未提升、回退或基线为 0 的结果均如实保留；零基线相对增益为 N/A，仍报告绝对百分点差。", ""])
    lines.extend(["- 各组按 F1/F2/P1/P2/P3 顺序运行，CPU 频率、缓存和后台负载可能影响延迟；父块参数不会改变相同子块索引的 query embedding，不应把组间 Search 延迟差直接解释为父子结构的提速。", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def exception_message(exc: BaseException) -> str:
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(exception_message(child) for child in exc.exceptions)
    return f"{type(exc).__name__}: {exc}"


async def run(args: argparse.Namespace) -> int:
    root, manifest, qrels, tokenizer_info = ROOT, {}, [], {}
    reasons, per_query = [], []
    manifest_path = root / "artifacts/manifest.json"
    if manifest_path.is_file():
        try:
            manifest = read_json(manifest_path)
            if not isinstance(manifest, dict):
                raise ValueError("manifest 必须为 JSON 对象")
        except (OSError, ValueError) as exc:
            reasons.append(f"索引 manifest 无效: {exc}")
            manifest = {}
    else:
        reasons.append("缺少 artifacts/manifest.json；先执行 python -m scripts.index")
    results = blank_results(manifest)
    if manifest and manifest.get("schema_version") != 1:
        reasons.append(f"不支持的 manifest.schema_version={manifest.get('schema_version')}；请重建索引")
    # These inexpensive input checks also run when the gold set is missing, so
    # users see independent model/index blockers together instead of one by one.
    try:
        from kb.embedding import resolve_model_path

        model_reference = (os.environ.get("KB_MODEL_PATH") or manifest.get("tokenizer", {}).get("path")
                           or manifest.get("tokenizer", {}).get("model_path")
                           or manifest.get("embedding", {}).get("model_path"))
        resolve_model_path(model_reference, root=root)
    except Exception as exc:
        reasons.append("本地 BGE-M3 模型/tokenizer 路径不可用: " + exception_message(exc))
    if manifest.get("status") == "READY":
        for group in EXPERIMENT_IDS:
            metadata = manifest.get("experiments", {}).get(group, {})
            for key in ("search_nodes", "faiss") + (("parent_nodes", "relations") if group.startswith("P") else ()):
                reference = metadata.get(key)
                try:
                    if not reference or not project_path(reference, root).is_file():
                        reasons.append(f"{group} 缺少索引/元数据 {key}；请重建索引")
                except EvaluationBlocked as exc:
                    reasons.append(str(exc))
    try:
        corpus, texts = load_corpus(root)
        if manifest.get("corpus", {}).get("digest") not in (None, corpus["digest"]):
            raise EvaluationBlocked("当前语料已变化，manifest.corpus.digest 不符；请重建索引与人工审核")
        # Still make the observed corpus available in the blocked report.
        manifest.setdefault("corpus", corpus)
        qrels = load_reviewed_qrels(project_path(args.qrels, root), project_path(args.review, root), corpus, texts)
    except Exception as exc:
        reasons.append(exception_message(exc))
    tokenizer = None
    if manifest.get("status") != "READY":
        reasons.append(f"索引未就绪: manifest.status={manifest.get('status', 'missing')}；请构建全部五组索引")
        for message in manifest.get("blocked_reasons", []):
            reasons.append(f"索引构建阻塞: {message}")
        if manifest.get("error"):
            reasons.append(f"索引构建错误: {manifest['error']}")
    if manifest.get("tokenizer", {}).get("fingerprint"):
        tokenizer_info = manifest["tokenizer"]
    if not reasons:
        try:
            tokenizer, tokenizer_info = load_fixed_tokenizer(manifest, root)
        except Exception as exc:
            reasons.append("模型/tokenizer 不可用: " + exception_message(exc))
    if reasons:
        for row in results:
            row["error"] = "; ".join(reasons)
    else:
        for row in results:
            group = row["group"]
            try:
                nodes = prepare_group(group, manifest, texts, root)
                samples = await evaluate_group(group, qrels, nodes, texts, tokenizer, args.repeats, args.warmup, root)
                aggregate(row, samples, len(qrels), args.repeats, args.warmup)
                per_query.extend(samples)
                print(f"{group}: OK, {len(qrels)} questions × {args.repeats} repeats", flush=True)
            except Exception as exc:
                message = f"{group} 实测失败: {exception_message(exc)}"
                reasons.append(message)
                row.update(status="BLOCKED", error=message)
        reasons.extend(verify_shared_search(results, per_query))
    output = project_path(args.output, root)
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "results.csv", RESULT_FIELDS, results)
    write_csv(output / "per_query.csv", QUERY_FIELDS, per_query)
    write_summary(output / "summary.md", results, per_query, manifest, reasons, tokenizer_info,
                  qrels, args.repeats, args.warmup)
    if qrels:
        review = read_json(project_path(args.review, root))
        with (output / "summary.md").open("a", encoding="utf-8") as handle:
            handle.write("\n## 标注来源与确认方式\n\n")
            handle.write(f"- 审核记录：{review.get('reviewer', '')}；时间 {review.get('reviewed_at', '')}。\n")
            handle.write(f"- 审核方式：{review.get('approval_method', '人工审核记录')}。\n")
            handle.write(f"- 记录说明：{review.get('notes', '')}\n")
            handle.write(f"- qrels SHA256：`{review.get('qrels_sha256', '')}`。\n")
            handle.write("- 本批问题的抽样及题型限制见 data/qrels/审核定稿.md 和 reports/annotation_review.md；指标是证据区间覆盖召回，不是回答正确率。\n")
    if reasons:
        print("BLOCKED: " + "\nBLOCKED: ".join(reasons), file=sys.stderr)
        print(f"已输出 {output / 'results.csv'}；未测字段留空，静态成本保留。", file=sys.stderr)
        return 2
    print(f"OK: {output / 'results.csv'}; {output / 'per_query.csv'}; {output / 'summary.md'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qrels", default="data/qrels/test.jsonl")
    parser.add_argument("--review", default="data/qrels/review.json")
    parser.add_argument("--output", default="reports")
    parser.add_argument("--repeats", type=int, default=3, help="Measured repetitions of every query")
    parser.add_argument("--warmup", type=int, default=1, help="Unmeasured repetitions of every query")
    args = parser.parse_args(argv)
    if args.repeats < 1 or args.warmup < 0:
        parser.error("--repeats must be >=1 and --warmup must be >=0")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
