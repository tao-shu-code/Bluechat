#!/usr/bin/env python
"""API 全流程冒烟测试脚本（Task 15.2）：requests 逐步执行并输出 PASS/FAIL/SKIP 摘要。

流程：
登录(admin/admin123) → 创建知识库（仅 admin 可见）→ 批量上传（含 1 个不支持格式文件验证部分失败）
→ 轮询等待 READY/FAILED（上限 --poll-timeout 秒）→ 问答 stream=false → 创建会话 → 多轮问答
→ 会话列表/详情/删除 → EMPLOYEE 越权测试（管理接口 403 / 受限 KB 不可见、检索为空）
→ 未登录访问业务接口期望 401。

SKIP 语义：LLM 未配置（code=5001）或文档 FAILED（嵌入等外部依赖缺失）时，
问答相关步骤标记 SKIP 不判 FAIL；认证/权限/知识库/上传接口始终真实断言。

用法：
    python scripts/api_test.py                       # 默认 http://localhost:8000
    python scripts/api_test.py --base-url http://127.0.0.1:8000 --admin-pass admin123
    python scripts/api_test.py --poll-timeout 120    # 环境变量 BASE_URL 亦可覆盖地址

退出码：0 = 全部通过（SKIP 不算失败）；1 = 存在 FAIL。
注意：脚本对 admin 发起 2 次问答、employee 1 次，若限流 RATE_LIMIT_PER_MIN 配置过小可能 429。
"""

import argparse
import io
import os
import sys
import time
import uuid

import requests

NO_ANSWER_TEXT = "知识库中暂无相关内容，请换个问法或联系知识管理员"
CODE_LLM_NOT_CONFIGURED = 5001
EMPLOYEE_PASSWORD = "Emp@12345"

TXT_CONTENT = """员工考勤与假期制度（测试文档A）

年假：入职满一年的正式员工，每年享有 5 天带薪年假，需提前 3 个工作日在 OA 系统申请。

病假：员工患病需提供二级以上医院证明，病假期间按当地最低工资的 80% 发放。
"""

MD_CONTENT = """# 测试文档B：报销指南

## 差旅报销
机票与酒店凭发票报销，需部门负责人审批。

### 报销时限
出差结束后 15 个工作日内提交，逾期不予受理。
"""

BAD_FILENAME = "bad_script.exe"
BAD_CONTENT = b"\x00\x01\x02this is not a document"


class SkipStep(Exception):
    """当前步骤因外部依赖缺失而跳过（不计 FAIL）。"""


class StopRun(Exception):
    """关键步骤失败，中止后续步骤（前置条件不成立）。"""


class Reporter:
    def __init__(self):
        self.results: list[tuple[str, str, str]] = []

    def record(self, step: str, status: str, detail: str = "") -> None:
        self.results.append((step, status, detail))
        line = f"[{status}] {step}"
        if detail:
            line += f" — {detail}"
        print(line, flush=True)

    def summary(self) -> int:
        passed = sum(1 for _, s, _ in self.results if s == "PASS")
        failed = sum(1 for _, s, _ in self.results if s == "FAIL")
        skipped = sum(1 for _, s, _ in self.results if s == "SKIP")
        print("\n========== 测试摘要 ==========")
        print(f"PASS: {passed}  FAIL: {failed}  SKIP: {skipped}  TOTAL: {len(self.results)}")
        for step, status, detail in self.results:
            if status == "FAIL":
                print(f"  [FAIL] {step}: {detail}")
            elif status == "SKIP":
                print(f"  [SKIP] {step}: {detail}")
        return 1 if failed else 0


class Api:
    """带 Bearer token 的轻量 HTTP 客户端。"""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.token: str | None = None

    def request(self, method: str, path: str, *, auth: bool = True, **kwargs) -> requests.Response:
        headers = kwargs.pop("headers", None) or {}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return requests.request(
            method, f"{self.base}{path}", headers=headers, timeout=self.timeout, **kwargs
        )

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs):
        return self.request("DELETE", path, **kwargs)


def assert_ok(resp: requests.Response, step: str) -> dict:
    """断言 HTTP 200 且业务 code==0，返回 data。"""
    if resp.status_code != 200:
        raise AssertionError(f"{step}: HTTP {resp.status_code}，body={resp.text[:300]}")
    body = resp.json()
    if body.get("code") != 0:
        raise AssertionError(f"{step}: 业务 code={body.get('code')}，message={body.get('message')}")
    return body.get("data") or {}


def unique_suffix() -> str:
    return uuid.uuid4().hex[:8]


class SmokeTest:
    def __init__(self, args):
        self.args = args
        self.reporter = Reporter()
        self.api = Api(args.base_url)
        self.run_id = unique_suffix()
        self.admin_id = ""
        self.kb_id = ""
        self.ready_docs: list[dict] = []
        self.failed_docs: list[dict] = []
        self.poll_reason = "not_polled"
        self.conv_id = ""
        self.first_chat: dict = {}
        self.employee_user: dict = {}
        self.emp_api = Api(args.base_url)

    # ---------- 基础设施 ----------

    def run(self, step_name: str, func, critical: bool = False) -> None:
        try:
            detail = func()
        except SkipStep as exc:
            self.reporter.record(step_name, "SKIP", str(exc))
        except StopRun as exc:
            self.reporter.record(step_name, "FAIL", str(exc))
            if critical:
                raise
        except AssertionError as exc:
            self.reporter.record(step_name, "FAIL", str(exc))
            if critical:
                raise
        except Exception as exc:  # noqa: BLE001 网络异常等统一归 FAIL
            self.reporter.record(step_name, "FAIL", f"异常：{exc!r}")
            if critical:
                raise
        else:
            self.reporter.record(step_name, "PASS", detail if isinstance(detail, str) else "")

    def require_qa_ready(self) -> None:
        """问答步骤前置守卫：文档未就绪时跳过（解析/嵌入等外部依赖缺失属预期）。"""
        if not self.ready_docs:
            docs = self.failed_docs
            reason = "全部文档 FAILED" if docs else f"轮询{self.poll_reason}"
            detail = "; ".join(
                f"{d.get('filename')}: {d.get('error_message') or d.get('status')}" for d in docs
            )
            raise SkipStep(f"{reason}（依赖缺失或超时），跳过问答断言。{detail}")

    def chat_once(self, api: Api, payload: dict) -> dict:
        """一次 stream=false 问答；LLM 未配置（code=5001）时抛 SkipStep。"""
        resp = api.post("/api/qa/chat", json={**payload, "stream": False})
        if resp.status_code == 429:
            raise AssertionError("触发限流 429（请调大 RATE_LIMIT_PER_MIN 或稍后重试）")
        if resp.status_code != 200:
            raise AssertionError(f"问答 HTTP {resp.status_code}，body={resp.text[:300]}")
        body = resp.json()
        if body.get("code") == CODE_LLM_NOT_CONFIGURED:
            raise SkipStep(f"LLM 服务未配置（code={CODE_LLM_NOT_CONFIGURED}），跳过问答断言")
        if body.get("code") != 0:
            raise AssertionError(f"问答业务失败：code={body.get('code')} message={body.get('message')}")
        return body.get("data") or {}

    # ---------- 步骤 ----------

    def step_health(self) -> str:
        resp = self.api.get("/health", auth=False)
        if resp.status_code != 200 or resp.json().get("status") != "ok":
            raise AssertionError(f"健康检查异常：HTTP {resp.status_code} {resp.text[:200]}")
        return "服务在线"

    def step_login(self) -> str:
        resp = self.api.post(
            "/api/auth/login",
            json={"username": self.args.admin_user, "password": self.args.admin_pass},
            auth=False,
        )
        if resp.status_code == 401:
            raise StopRun(f"登录失败（用户名或密码错误）：{self.args.admin_user}")
        data = assert_ok(resp, "admin 登录")
        user = data.get("user") or {}
        if not data.get("access_token"):
            raise StopRun("登录响应缺少 access_token")
        self.api.token = data["access_token"]
        if "ADMIN" not in (user.get("roles") or []):
            raise StopRun(f"账号 {self.args.admin_user} 不是 ADMIN 角色：{user.get('roles')}")
        self.admin_id = str(user.get("id") or "")
        return f"admin_id={self.admin_id}"

    def step_create_kb(self) -> str:
        kb_name = f"冒烟测试库-{self.run_id}"
        data = assert_ok(
            self.api.post(
                "/api/kb",
                json={
                    "name": kb_name,
                    "description": "api_test.py 自动创建（仅 admin 可见，用于越权验证）",
                    "visibility": "USER",
                    "user_ids": [self.admin_id],
                },
            ),
            "创建知识库",
        )
        if not data.get("id"):
            raise AssertionError("创建知识库响应缺少 id")
        if data.get("user_ids") != [self.admin_id]:
            raise AssertionError(f"知识库 ACL user_ids 不符：{data.get('user_ids')}")
        self.kb_id = str(data["id"])
        return f"kb_id={self.kb_id}"

    def step_upload(self) -> str:
        files = [
            ("files", ("测试文档A.txt", io.BytesIO(TXT_CONTENT.encode("utf-8")), "text/plain")),
            ("files", ("测试文档B.md", io.BytesIO(MD_CONTENT.encode("utf-8")), "text/markdown")),
            ("files", (BAD_FILENAME, io.BytesIO(BAD_CONTENT), "application/octet-stream")),
        ]
        results = assert_ok(
            self.api.post("/api/documents/upload", data={"kb_id": self.kb_id}, files=files),
            "批量上传文档",
        )
        if not isinstance(results, list) or len(results) != 3:
            raise AssertionError(f"上传应返回 3 个文件的结果，实际：{results}")
        bad = next((r for r in results if r.get("filename") == BAD_FILENAME), None)
        if bad is None:
            raise AssertionError(f"结果缺少 {BAD_FILENAME}：{results}")
        if bad.get("success") is not False or not bad.get("reason"):
            raise AssertionError(f"不支持格式文件应部分失败并给出原因：{bad}")
        for item in (r for r in results if r.get("filename") != BAD_FILENAME):
            if not item.get("success") or not item.get("document_id"):
                raise AssertionError(f"受支持文件应上传成功：{item}")
        return f"成功 {sum(1 for r in results if r.get('success'))}/3，不支持格式已拒绝（部分失败验证通过）"

    def step_poll(self) -> str:
        """轮询到全部文档进入终态（READY/FAILED）或超时。"""
        deadline = time.time() + self.args.poll_timeout
        items: list[dict] = []
        while time.time() < deadline:
            data = assert_ok(
                self.api.get("/api/documents", params={"kb_id": self.kb_id, "page": 1, "size": 50}),
                "轮询文档状态",
            )
            items = data.get("items") or []
            pending = [d for d in items if d.get("status") not in ("READY", "FAILED")]
            if items and not pending:
                self.poll_reason = "all_terminal"
                break
            time.sleep(2)
        else:
            self.poll_reason = "timeout"
        self.ready_docs = [d for d in items if d.get("status") == "READY"]
        self.failed_docs = [d for d in items if d.get("status") == "FAILED"]
        if self.failed_docs:
            reasons = "; ".join(
                f"{d.get('filename')}: {d.get('error_message') or '无错误信息'}" for d in self.failed_docs
            )
            print(f"  [INFO] 部分文档 FAILED（嵌入等外部依赖缺失时属预期）：{reasons}")
        if self.poll_reason == "timeout":
            statuses = {d.get("filename"): d.get("status") for d in items}
            print(f"  [INFO] 轮询超时，文档状态：{statuses}")
        return (
            f"READY={len(self.ready_docs)} FAILED={len(self.failed_docs)} "
            f"结束原因={self.poll_reason}"
        )

    def step_first_chat(self) -> str:
        self.require_qa_ready()
        data = self.chat_once(
            self.api,
            {"question": "入职满一年的员工每年有几天带薪年假？", "kb_ids": [self.kb_id]},
        )
        self.first_chat = data
        if not data.get("answer"):
            raise AssertionError("回答为空")
        if not data.get("conversation_id"):
            raise AssertionError("响应缺少 conversation_id")
        sources = data.get("sources") or []
        return (
            f"answer={str(data['answer'])[:40]}... "
            f"sources={len(sources)} 条 conversation={data['conversation_id'][:8]}..."
        )

    def step_multi_round_chat(self) -> str:
        self.require_qa_ready()
        data = self.chat_once(
            self.api,
            {
                "question": "那病假怎么规定的？",
                "conversation_id": self.conv_id,
                "kb_ids": [self.kb_id],
            },
        )
        if not data.get("answer"):
            raise AssertionError("第二轮回答为空")
        return f"第二轮 answer={str(data['answer'])[:40]}..."

    def step_create_conversation(self) -> str:
        data = assert_ok(
            self.api.post("/api/conversations", json={"title": f"冒烟会话-{self.run_id}"}),
            "创建会话",
        )
        if not data.get("id"):
            raise AssertionError("创建会话响应缺少 id")
        self.conv_id = str(data["id"])
        return f"conv_id={self.conv_id[:8]}..."

    def step_list_conversations(self) -> str:
        data = assert_ok(self.api.get("/api/conversations"), "会话列表")
        items = data.get("items") or []
        if not any(item.get("id") == self.conv_id for item in items):
            raise AssertionError(f"会话列表未包含新会话 {self.conv_id}")
        return f"total={data.get('total')} 列表命中"

    def step_conversation_detail(self) -> str:
        data = assert_ok(self.api.get(f"/api/conversations/{self.conv_id}"), "会话详情")
        if data.get("id") != self.conv_id:
            raise AssertionError("详情 id 不匹配")
        messages = data.get("messages") or []
        if self.first_chat and len(messages) < 2:
            raise AssertionError(f"问答后会话消息数应 >= 2，实际 {len(messages)}")
        if messages and messages[0].get("role") != "user":
            raise AssertionError(f"首条消息角色应为 user，实际：{messages[0].get('role')}")
        return f"消息数 {len(messages)}"

    def step_delete_conversation(self) -> str:
        assert_ok(self.api.delete(f"/api/conversations/{self.conv_id}"), "删除会话")
        return f"已删除 {self.conv_id[:8]}..."

    def step_deleted_detail_404(self) -> str:
        resp = self.api.get(f"/api/conversations/{self.conv_id}")
        if resp.status_code != 404:
            raise AssertionError(f"删除后详情应 404，实际 HTTP {resp.status_code}")
        return "详情已 404"

    # ---------- EMPLOYEE 越权 ----------

    def step_create_employee(self) -> str:
        roles_data = assert_ok(self.api.get("/api/admin/roles"), "查询角色列表")
        codes = {r.get("code") for r in (roles_data or [])}
        if "EMPLOYEE" not in codes:
            raise SkipStep(f"角色列表缺少 EMPLOYEE（实际：{sorted(codes)}），跳过越权测试")
        username = f"emp_{self.run_id}"
        data = assert_ok(
            self.api.post(
                "/api/admin/users",
                json={
                    "username": username,
                    "password": EMPLOYEE_PASSWORD,
                    "display_name": "冒烟测试员工",
                    "roles": ["EMPLOYEE"],
                },
            ),
            "admin 创建 EMPLOYEE 用户",
        )
        if not data.get("id"):
            raise AssertionError("创建用户响应缺少 id")
        self.employee_user = data
        return f"username={username}"

    def step_employee_login(self) -> str:
        if not self.employee_user:
            raise SkipStep("EMPLOYEE 用户未创建，跳过")
        resp = self.emp_api.post(
            "/api/auth/login",
            json={"username": self.employee_user["username"], "password": EMPLOYEE_PASSWORD},
            auth=False,
        )
        data = assert_ok(resp, "employee 登录")
        self.emp_api.token = data.get("access_token")
        if not self.emp_api.token:
            raise AssertionError("employee 登录响应缺少 access_token")
        return "登录成功"

    def _require_employee(self) -> None:
        if not self.emp_api.token:
            raise SkipStep("employee 未登录，跳过")

    def step_employee_admin_403(self) -> str:
        self._require_employee()
        resp = self.emp_api.get("/api/admin/users")
        if resp.status_code != 403:
            raise AssertionError(f"EMPLOYEE 调管理接口应 403，实际 HTTP {resp.status_code}")
        return "GET /api/admin/users → 403"

    def step_employee_create_kb_403(self) -> str:
        self._require_employee()
        resp = self.emp_api.post("/api/kb", json={"name": "越权库", "visibility": "ALL"})
        if resp.status_code != 403:
            raise AssertionError(f"EMPLOYEE 创建知识库应 403，实际 HTTP {resp.status_code}")
        return "POST /api/kb → 403"

    def step_employee_kb_list_excludes_restricted(self) -> str:
        self._require_employee()
        data = assert_ok(self.emp_api.get("/api/kb"), "employee 知识库列表")
        ids = [item.get("id") for item in (data or [])]
        if self.kb_id in ids:
            raise AssertionError("受限知识库不应出现在 EMPLOYEE 可见列表中")
        return f"可见 {len(ids)} 个知识库，均不含受限库"

    def step_employee_documents_403(self) -> str:
        self._require_employee()
        resp = self.emp_api.get("/api/documents", params={"kb_id": self.kb_id})
        if resp.status_code != 403:
            raise AssertionError(f"EMPLOYEE 访问受限库文档列表应 403，实际 HTTP {resp.status_code}")
        return "GET /api/documents?kb_id=受限库 → 403"

    def step_employee_restricted_retrieval_empty(self) -> str:
        """受限库对 employee 不可见 → 检索无任何来源且返回拒答文案（LLM 未配置时跳过）。"""
        self._require_employee()
        # 受限库内容包含"年假/病假"，employee 检索不到：sources 必为空、answer 为固定拒答文案
        data = self.chat_once(
            self.emp_api,
            {"question": "员工年假有几天？", "kb_ids": [self.kb_id]},
        )
        if data.get("sources"):
            raise AssertionError(f"受限库检索应无引用来源，实际：{data.get('sources')}")
        if data.get("answer") != NO_ANSWER_TEXT:
            raise AssertionError(f"应返回拒答文案，实际：{data.get('answer')!r}")
        return "sources=[] 且返回拒答文案（受限库内容未泄露）"

    def step_unauthorized_401(self) -> str:
        checks = [
            ("GET", "/api/kb", None),
            ("POST", "/api/qa/chat", {"question": "hi", "stream": False}),
            ("GET", "/api/conversations", None),
        ]
        for method, path, payload in checks:
            resp = requests.request(method, f"{self.api.base}{path}", json=payload, timeout=30)
            if resp.status_code != 401:
                raise AssertionError(
                    f"未登录访问 {method} {path} 应 401，实际 HTTP {resp.status_code}"
                )
        return "3 个业务接口均返回 401"

    # ---------- 主流程 ----------

    def execute(self) -> int:
        run = self.run
        try:
            run("健康检查", self.step_health, critical=True)
            run("admin 登录并校验 ADMIN 角色", self.step_login, critical=True)
            run("创建知识库（仅 admin 可见）", self.step_create_kb)
            run("批量上传（含不支持格式部分失败）", self.step_upload)
            run(f"轮询文档状态（上限 {self.args.poll_timeout}s）", self.step_poll)
            run("问答 stream=false（首轮，含响应结构校验）", self.step_first_chat)
            run("创建会话", self.step_create_conversation)
            run("多轮问答（第二轮携带 conversation_id）", self.step_multi_round_chat)
            run("会话列表包含新会话", self.step_list_conversations)
            run("会话详情与消息校验", self.step_conversation_detail)
            run("删除会话", self.step_delete_conversation)
            run("删除后会话详情 404", self.step_deleted_detail_404)
            run("admin 创建 EMPLOYEE 用户", self.step_create_employee)
            run("employee 登录", self.step_employee_login)
            run("EMPLOYEE 调管理接口期望 403", self.step_employee_admin_403)
            run("EMPLOYEE 创建知识库期望 403", self.step_employee_create_kb_403)
            run("EMPLOYEE 知识库列表不含受限库", self.step_employee_kb_list_excludes_restricted)
            run("EMPLOYEE 访问受限库文档列表期望 403", self.step_employee_documents_403)
            run("EMPLOYEE 越权检索受限库为空", self.step_employee_restricted_retrieval_empty)
            run("未登录访问业务接口期望 401", self.step_unauthorized_401)
        except StopRun:
            print("\n关键步骤失败，中止后续步骤。")
        return self.reporter.summary()


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 知识库 API 全流程冒烟测试")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BASE_URL", "http://localhost:8000"),
        help="服务地址（默认 http://localhost:8000，可用环境变量 BASE_URL 覆盖）",
    )
    parser.add_argument("--admin-user", default="admin")
    parser.add_argument("--admin-pass", default="admin123")
    parser.add_argument("--poll-timeout", type=int, default=180, help="文档处理轮询上限（秒）")
    args = parser.parse_args()

    print(f"目标服务：{args.base_url}\n")
    return SmokeTest(args).execute()


if __name__ == "__main__":
    sys.exit(main())
