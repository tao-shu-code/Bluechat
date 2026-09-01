"""PDF 结构化解析单元测试：字号推断标题 + 分节聚合 + 页眉页脚剔除 + 图片 OCR。

用 pymupdf 现场构造带字号层级/页眉页脚/表格/图片的 PDF，验证解析产出。
OCR 用 fake 引擎 mock，不依赖真实模型推理。
"""

import pymupdf
import pytest

from app.document import parsers
from app.document.parsers import _parse_pdf


class FakeOCR:
    """RapidOCR 替身：固定返回两行识别文本（与真实引擎同构的 (result, elapse)）。"""

    def __call__(self, img):
        result = [
            [[[10, 10], [200, 10], [200, 40], [10, 40]], "扫描文本内容", 0.95],
            [[[10, 50], [200, 50], [200, 80], [10, 80]], "第二行文字", 0.93],
        ]
        return result, None


def _make_image_page_pdf(monkeypatch, *, with_text: bool) -> bytes:
    """构造含图片的 PDF；with_text=True 时附加一行真实文本（混排页）。"""
    monkeypatch.setattr(parsers, "_get_ocr_engine", lambda: FakeOCR())
    doc = pymupdf.open()
    page = doc.new_page()
    if with_text:
        page.insert_text((60, 60), "混排页正文", fontsize=10.5, fontname="china-s")
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 60))
    pix.set_rect(pix.irect, (200, 200, 200))
    page.insert_image(pymupdf.Rect(50, 100, 300, 300), pixmap=pix)
    return doc.tobytes()


def _make_pdf(pages: list[list[tuple[str, float]]]) -> bytes:
    """构造 PDF：每页若干 (文本, 字号) 行。中文用内置 CJK 字体。"""
    doc = pymupdf.open()
    for page_lines in pages:
        page = doc.new_page()
        y = 60.0
        for text, size in page_lines:
            page.insert_text((60, y), text, fontsize=size, fontname="china-s")
            y += size + 8
    return doc.tobytes()


H1, H2, BODY = 16.0, 13.0, 10.5


class TestPdfStructured:
    def test_heading_detection_and_sections(self):
        content = _make_pdf(
            [
                [
                    ("第一章 总则", H1),
                    ("内部审计的目的是规范审计工作。", BODY),
                    ("第一条 目的", H2),
                    ("为加强管理制定本制度。", BODY),
                ],
                [
                    ("第二章 职责", H1),
                    ("审计部负责内部审计。", BODY),
                ],
            ]
        )
        docs = _parse_pdf(content)
        assert len(docs) == 3  # 两个 H1 章节，第二个 H1 下一个子章节
        assert docs[0].page_content.startswith("第一章 总则")
        assert docs[0].metadata["title_path"] == "第一章 总则"
        assert docs[1].metadata["title_path"] == "第一章 总则 > 第一条 目的"
        assert docs[2].metadata["title_path"] == "第二章 职责"
        assert "审计部负责内部审计。" in docs[2].page_content

    def test_repeated_header_footer_removed(self):
        pages = [
            [
                ("内部审计管理制度", BODY),  # 每页顶部重复 → 页眉剔除
                ("第一章 总则", H1),
                ("内容一。", BODY),
                ("- 1 -", BODY),  # 页码行剔除
            ],
            [
                ("内部审计管理制度", BODY),
                ("第二章 职责", H1),
                ("内容二。", BODY),
                ("- 2 -", BODY),
            ],
            [
                ("内部审计管理制度", BODY),
                ("第三章 程序", H1),
                ("内容三。", BODY),
                ("- 3 -", BODY),
            ],
        ]
        docs = _parse_pdf(_make_pdf(pages))
        assert docs, "不应解析为空"
        for doc in docs:
            assert "内部审计管理制度" not in doc.page_content
            assert "- 1 -" not in doc.page_content and "- 2 -" not in doc.page_content

    def test_consecutive_headings_merged(self):
        content = _make_pdf(
            [
                [
                    ("第一章 总则", H1),  # 无正文，紧跟 H2
                    ("第一条 目的", H2),
                    ("制定本制度。", BODY),
                ]
            ]
        )
        docs = _parse_pdf(content)
        assert len(docs) == 1  # 不产生孤立标题 chunk
        assert docs[0].page_content.splitlines()[0] == "第一章 总则"
        assert docs[0].metadata["title_path"] == "第一章 总则 > 第一条 目的"

    def test_body_lines_never_headings(self):
        content = _make_pdf(
            [
                [
                    ("唯一章节", H1),
                    ("这一行很长所以不是标题，即使它出现在页面边缘位置也不应被误判为标题或页眉。", BODY),
                ]
            ]
        )
        docs = _parse_pdf(content)
        assert len(docs) == 1
        assert "这一行很长" in docs[0].page_content

    def test_table_extracted_as_markdown(self):
        # 画线框表格（find_tables 默认按线条识别）+ 单元格文本
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((60, 50), "第五章 审计机构", fontsize=16, fontname="china-s")
        col_x = [60, 160, 300]
        row_y = [80, 110, 140]
        for x in col_x:
            page.draw_line((x, row_y[0]), (x, row_y[-1]))
        for y in row_y:
            page.draw_line((col_x[0], y), (col_x[-1], y))
        cells = [
            ("部门", "职责"),
            ("审计部", "内部审计"),
            ("风控部", "风险管理"),
        ]
        for r in range(3):
            for c in range(2):
                page.insert_text(
                    (col_x[c] + 6, row_y[r] + 20),
                    cells[r][c],
                    fontsize=10.5,
                    fontname="china-s",
                )
        content = doc.tobytes()

        docs = _parse_pdf(content)
        assert docs, "不应解析为空"
        table_doc = next((d for d in docs if "|" in d.page_content), None)
        assert table_doc is not None, f"表格未转 markdown: {[d.page_content for d in docs]}"
        assert "审计部" in table_doc.page_content
        assert "内部审计" in table_doc.page_content
        # 表格在标题章节内：markdown 行作为正文并入章节
        assert table_doc.page_content.startswith("第五章 审计机构")

    def test_ocr_image_only_page(self, monkeypatch):
        # 纯图片页（无文本层）：整页 OCR 兜底
        content = _make_image_page_pdf(monkeypatch, with_text=False)
        docs = _parse_pdf(content)
        assert docs, "扫描页 OCR 后不应为空"
        joined = "\n".join(d.page_content for d in docs)
        assert "扫描文本内容" in joined
        assert "第二行文字" in joined

    def test_ocr_embedded_image_in_mixed_page(self, monkeypatch):
        # 图文混排页：文本正常结构化 + 图片 OCR 文本并入
        content = _make_image_page_pdf(monkeypatch, with_text=True)
        docs = _parse_pdf(content)
        joined = "\n".join(d.page_content for d in docs)
        assert "混排页正文" in joined
        assert "扫描文本内容" in joined

    def test_ocr_disabled_image_page_empty(self, monkeypatch):
        # OCR 关闭：纯图片页解析为空（现状行为）
        monkeypatch.setattr(parsers, "_get_ocr_engine", lambda: FakeOCR())
        from app.core.config import settings

        monkeypatch.setattr(settings, "PDF_OCR_ENABLED", False)
        content = _make_image_page_pdf(monkeypatch, with_text=False)
        assert _parse_pdf(content) == []

    def test_invalid_pdf_fallback(self):
        # 非法 PDF 字节：结构化失败 → 回退纯文本路径（pypdf 抛错则原样抛出由任务层处置）
        with pytest.raises(Exception):
            _parse_pdf(b"not a pdf at all")
