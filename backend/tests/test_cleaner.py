"""文档清洗单元测试：字符规范化 / 页码行剔除 / 跨页页眉页脚识别。

不依赖外部服务，直接测试 cleaner 的纯函数行为与 clean_documents 入口分发。
"""

from langchain_core.documents import Document

import app.document.cleaner as cleaner
from app.document.cleaner import clean_documents, normalize_text


class TestNormalizeText:
    def test_fullwidth_space_and_crlf(self):
        assert normalize_text("你好\u3000世界\r\n第二行\r结束") == "你好 世界\n第二行\n结束"

    def test_zero_width_and_control_chars(self):
        text = "隐\u200b形\ufeff字\x07符\t保留制表"
        assert normalize_text(text) == "隐形字符\t保留制表"
        assert "\x07" not in normalize_text("bad\x00\x1fchars")

    def test_trailing_whitespace_removed_keep_newlines(self):
        assert normalize_text("行一  \n行二 \t\n中间行") == "行一\n行二\n中间行"

    def test_empty(self):
        assert normalize_text("") == ""


class TestPdfHeadersFooters:
    def test_repeated_header_removed(self):
        pages = [
            "内部审计管理制度\n正文第一页内容",
            "内部审计管理制度\n正文第二页内容",
            "内部审计管理制度\n正文第三页内容",
        ]
        cleaned = cleaner._strip_headers_footers(pages)
        assert all("内部审计管理制度" not in page for page in cleaned)
        assert "正文第一页内容" in cleaned[0]

    def test_body_never_touched(self):
        # 重复行出现在页面中间（非边缘）时不剔除
        pages = [
            "页眉A\n第一章 总则\n第一章 总则\n内容",
            "页眉A\n内容B\n第一章 总则\n内容C",
            "页眉A\n内容D\n内容E\n内容F",
        ]
        cleaned = cleaner._strip_headers_footers(pages)
        assert "第一章 总则" in cleaned[0]
        assert "页眉A" not in cleaned[0]

    def test_page_number_lines_removed(self):
        pages = [
            "第一章 总则\n- 1 -",
            "第二章 职责\n第 2 页",
            "第三章 程序\nPage 3",
        ]
        cleaned = cleaner._strip_headers_footers(pages)
        assert cleaned[0] == "第一章 总则"
        assert cleaned[1] == "第二章 职责"
        assert cleaned[2] == "第三章 程序"

    def test_few_pages_no_repeat_removal(self):
        # 不足 3 页时重复行不判定为页眉（样本太少，避免误删）
        pages = ["公司简介\n内容一", "公司简介\n内容二"]
        cleaned = cleaner._strip_headers_footers(pages)
        assert "公司简介" in cleaned[0]


class TestCleanDocuments:
    def test_pdf_path_drops_empty_pages(self):
        docs = [
            Document(page_content="页眉X\n正文", metadata={"page_number": 1}),
            Document(page_content="页眉X", metadata={"page_number": 2}),
            Document(page_content="页眉X\n正文三", metadata={"page_number": 3}),
            Document(page_content="页眉X\n正文四", metadata={"page_number": 4}),
        ]
        result = clean_documents(docs, "pdf")
        assert len(result) == 3  # 第 2 页清洗后为空被丢弃
        assert all("页眉X" not in d.page_content for d in result)
        assert result[0].metadata["page_number"] == 1

    def test_non_pdf_normalizes_only(self):
        docs = [Document(page_content="文本\u3000一\r\n", metadata={"title_path": "t"})]
        result = clean_documents(docs, "docx")
        assert result[0].page_content == "文本 一"
        assert result[0].metadata["title_path"] == "t"

    def test_empty_docs(self):
        assert clean_documents([], "pdf") == []
