#!/usr/bin/env python
"""QA 接口压测脚本（Task 15.3）：asyncio + httpx 对 POST /api/qa/chat (stream=false) 并发压测。

统计成功请求的延迟 P50 / P95 / P99 / 平均 / 最小 / 最大、吞吐与错误率（按状态码分类，
429 限流单独列出）。仅编写脚本，可按需实际执行。

用法：
    python scripts/load_test.py --concurrency 50 --total 200
    python scripts/load_test.py --base-url http://127.0.0.1:8000 -c 20 -n 100 \\
        --username admin --password admin123 --kb-ids <kb_id1>,<kb_id2> \\
        --question "入职满一年的员工每年有几天带薪年假？" --timeout 60

说明：
- 先用账号密码登录换取 token（或直接传 --token 复用已有凭证）；
- 默认限流 RATE_LIMIT_PER_MIN=10/分钟，压测前请调大该配置，否则将出现大量 429（报告中单独计数）；
- 延迟统计仅计入"成功"请求（HTTP 200 且业务 code=0）。
"""

import argparse
import asyncio
import math
import os
import sys
import time
from collections import Counter

import httpx

DEFAULT_QUESTION = "入职满一年的员工每年有几天带薪年假？"


def percentile(sorted_values: list[float], p: float) -> float:
    """线性插值百分位（sorted_values 需已升序）。"""
    if not sorted_values:
        return 0.0
    rank = (len(sorted_values) - 1) * p / 100.0
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_values[int(rank)]
    weight = rank - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


async def login(base_url: str, username: str, password: str, timeout: float) -> str:
    """登录换取 Bearer token。"""
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{base_url}/api/auth/login", json={"username": username, "password": password}
        )
    if resp.status_code != 200:
        raise SystemExit(f"登录失败：HTTP {resp.status_code} {resp.text[:200]}")
    body = resp.json()
    token = (body.get("data") or {}).get("access_token")
    if body.get("code") != 0 or not token:
        raise SystemExit(f"登录失败：code={body.get('code')} message={body.get('message')}")
    return token


async def run_one(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    payload: dict,
    results: list,
    index: int,
) -> None:
    """单次请求：记录 (耗时, 状态码, 是否成功)。状态码 0 表示连接/超时异常。"""
    start = time.perf_counter()
    try:
        resp = await client.post(url, json=payload, headers=headers)
        elapsed = time.perf_counter() - start
        ok = resp.status_code == 200 and (resp.json().get("code") == 0)
        results.append((elapsed, resp.status_code, ok))
    except httpx.HTTPError:
        results.append((time.perf_counter() - start, 0, False))


async def worker(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    payload: dict,
    results: list,
    queue: asyncio.Queue,
) -> None:
    """从队列取任务号执行，直到队列取尽（None 哨兵）。"""
    while True:
        index = await queue.get()
        try:
            if index is None:
                return
            await run_one(client, url, headers, payload, results, index)
        finally:
            queue.task_done()


def print_report(
    results: list, total: int, concurrency: int, wall_seconds: float, question: str
) -> None:
    latencies_ok = sorted(elapsed for elapsed, _, ok in results if ok)
    status_counter = Counter(status for _, status, _ in results)
    ok_count = len(latencies_ok)
    error_count = total - ok_count
    error_rate = error_count / total * 100 if total else 0.0

    print("\n================ 压测报告 ================")
    print(f"接口       : POST /api/qa/chat (stream=false)")
    print(f"问题       : {question}")
    print(f"并发数     : {concurrency}")
    print(f"总请求数   : {total}")
    print(f"总耗时     : {wall_seconds:.2f}s")
    print(f"吞吐量     : {total / wall_seconds:.2f} req/s（成功 {ok_count / wall_seconds:.2f} req/s）")
    print(f"成功       : {ok_count}")
    print(f"失败       : {error_count}（错误率 {error_rate:.1f}%）")
    if status_counter:
        breakdown = ", ".join(
            f"{status or '连接异常'}: {count}" for status, count in sorted(status_counter.items())
        )
        print(f"状态码分布 : {breakdown}")
        if status_counter.get(429):
            print("提示       : 检测到 429 限流，请调大 RATE_LIMIT_PER_MIN 后重测。")
    print("------------------------------------------")
    if latencies_ok:
        avg = sum(latencies_ok) / len(latencies_ok)
        print("成功请求延迟：")
        print(f"  最小 : {latencies_ok[0] * 1000:8.1f} ms")
        print(f"  P50  : {percentile(latencies_ok, 50) * 1000:8.1f} ms")
        print(f"  P95  : {percentile(latencies_ok, 95) * 1000:8.1f} ms")
        print(f"  P99  : {percentile(latencies_ok, 99) * 1000:8.1f} ms")
        print(f"  最大 : {latencies_ok[-1] * 1000:8.1f} ms")
        print(f"  平均 : {avg * 1000:8.1f} ms")
    else:
        print("无成功请求，无延迟统计（请检查服务状态 / 认证 / 限流配置）。")
    print("==========================================")

    if ok_count == 0:
        raise SystemExit(1)


async def run_load(args: argparse.Namespace) -> None:
    base_url = args.base_url.rstrip("/")
    token = args.token or await login(base_url, args.username, args.password, args.timeout)
    headers = {"Authorization": f"Bearer {token}"}
    kb_ids = [item.strip() for item in args.kb_ids.split(",") if item.strip()] or None
    payload = {"question": args.question, "kb_ids": kb_ids, "stream": False}
    url = f"{base_url}/api/qa/chat"

    results: list = []
    queue: asyncio.Queue = asyncio.Queue()
    for index in range(args.total):
        queue.put_nowait(index)
    for _ in range(args.concurrency):
        queue.put_nowait(None)  # 哨兵：通知 worker 退出

    limits = httpx.Limits(max_connections=args.concurrency + 10, max_keepalive_connections=args.concurrency)
    timeout = httpx.Timeout(args.timeout)
    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        workers = [
            asyncio.create_task(
                worker(client, url, headers, payload, results, queue)
            )
            for _ in range(args.concurrency)
        ]
        started = time.perf_counter()
        await asyncio.gather(*workers)
        wall_seconds = time.perf_counter() - started

    print_report(results, args.total, args.concurrency, wall_seconds, args.question)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QA 接口压测（stream=false）")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BASE_URL", "http://localhost:8000"),
        help="服务地址（默认 http://localhost:8000）",
    )
    parser.add_argument("-c", "--concurrency", type=int, default=50, help="并发数（默认 50）")
    parser.add_argument("-n", "--total", type=int, default=200, help="总请求数（默认 200）")
    parser.add_argument("--username", default="admin", help="登录用户名")
    parser.add_argument("--password", default="admin123", help="登录密码")
    parser.add_argument("--token", default="", help="直接使用已有 Bearer token（跳过登录）")
    parser.add_argument("--kb-ids", default="", help="逗号分隔的知识库 ID（留空检索全部可见 KB）")
    parser.add_argument("--question", default=DEFAULT_QUESTION, help="提问内容")
    parser.add_argument("--timeout", type=float, default=60.0, help="单请求超时秒数（默认 60）")
    args = parser.parse_args()
    if args.concurrency <= 0 or args.total <= 0:
        parser.error("并发数与总请求数必须为正整数")
    if args.total < args.concurrency:
        args.concurrency = args.total
    return args


if __name__ == "__main__":
    try:
        asyncio.run(run_load(parse_args()))
    except KeyboardInterrupt:
        print("\n已手动中断。")
        sys.exit(130)
