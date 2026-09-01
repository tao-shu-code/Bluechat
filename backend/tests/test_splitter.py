"""切分模块单元测试（Task 5）：长度上限 / overlap / markdown 标题 title_path / 自定义参数。"""

from langchain_core.documents import Document

from app.chunking.splitter import split_documents


def _plain_text(total_chars: int) -> str:
    """构造不含任何分隔符（换行/空格/句号）的纯数字文本，走 "" 兜底字符切分，便于精确断言。"""
    return "".join(str(i % 10) for i in range(total_chars))


class TestRecursiveSplit:
    def test_chunk_size_upper_bound(self):
        docs = [Document(page_content=_plain_text(600))]
        items = split_documents(docs, chunk_size=100, chunk_overlap=20)
        assert items, "长文本应产出至少一个 chunk"
        for item in items:
            assert 0 < len(item["content"]) <= 100
            assert item["title_path"] is None
            assert item["page_number"] is None

    def test_consecutive_chunks_overlap(self):
        docs = [Document(page_content=_plain_text(600))]
        items = split_documents(docs, chunk_size=100, chunk_overlap=20)
        contents = [item["content"] for item in items]
        assert len(contents) > 1
        for prev, nxt in zip(contents, contents[1:]):
            # 相邻 chunk 首尾重叠 20 字符
            assert prev[-20:] == nxt[:20]

    def test_metadata_inherited_for_plain_docs(self):
        doc = Document(
            page_content=_plain_text(300),
            metadata={"title_path": "产品手册 > 第一章", "page_number": 3},
        )
        items = split_documents([doc], chunk_size=80, chunk_overlap=10)
        assert items
        assert all(item["title_path"] == "产品手册 > 第一章" for item in items)
        assert all(item["page_number"] == 3 for item in items)

    def test_whitespace_only_content_filtered(self):
        docs = [Document(page_content="   \n\n  ")]
        assert split_documents(docs, chunk_size=100, chunk_overlap=0) == []


class TestMarkdownSplit:
    MD_TEXT = """# 入职指南
欢迎加入公司，请先阅读本节内容。

## 办公设备
电脑与工牌由 IT 部门统一发放，请于入职当天领取。

### 门禁申请
门禁权限需要在 OA 系统提交申请，审批通过后自动开通。

## 考勤制度
迟到早退按公司考勤规定处理。
"""

    def test_title_path_from_headers(self):
        docs = [Document(page_content=self.MD_TEXT)]
        items = split_documents(docs, chunk_size=200, chunk_overlap=0, is_markdown=True)
        title_paths = {item["title_path"] for item in items}
        assert "入职指南" in title_paths
        assert "入职指南 > 办公设备" in title_paths
        assert "入职指南 > 办公设备 > 门禁申请" in title_paths
        assert "入职指南 > 考勤制度" in title_paths

    def test_md_chunks_keep_header_and_nonempty(self):
        docs = [Document(page_content=self.MD_TEXT)]
        items = split_documents(docs, chunk_size=200, chunk_overlap=0, is_markdown=True)
        assert items
        for item in items:
            assert item["content"].strip()  # 空白 chunk 已过滤
            assert item["page_number"] is None
        # strip_headers=False：chunk 内容保留标题行
        contents = "\n".join(item["content"] for item in items)
        assert "## 办公设备" in contents


class TestCustomParams:
    def test_smaller_size_produces_more_chunks(self):
        docs = [Document(page_content=_plain_text(400))]
        small = split_documents(docs, chunk_size=50, chunk_overlap=10)
        large = split_documents(docs, chunk_size=200, chunk_overlap=0)
        assert all(len(i["content"]) <= 50 for i in small)
        assert all(len(i["content"]) <= 200 for i in large)
        assert len(small) > len(large)

    def test_overlap_clamped_when_out_of_range(self):
        # overlap >= chunk_size 时被钳制为 chunk_size-1，不应抛异常
        docs = [Document(page_content=_plain_text(120))]
        items = split_documents(docs, chunk_size=50, chunk_overlap=999)
        assert items
        assert all(len(i["content"]) <= 50 for i in items)

    def test_zero_overlap_no_overlap(self):
        docs = [Document(page_content=_plain_text(300))]
        items = split_documents(docs, chunk_size=100, chunk_overlap=0)
        contents = [item["content"] for item in items]
        for prev, nxt in zip(contents, contents[1:]):
            assert prev[-1:] != nxt[:1]
