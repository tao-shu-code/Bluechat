"""文档文本清洗（解析后、切分前统一执行）。

清洗原则：只删除确定的噪音，不做激进改写，避免破坏正文语义。

- normalize_text（所有格式）：全角空格/零宽字符/控制字符规范化、行尾空白去除；
- PDF 额外清洗：页边缘的页码行剔除 + 跨页重复行（页眉/页脚）识别剔除——
  仅处理每页前/后各 2 行的"边缘区域"，正文行永不改动；
- 清洗后整页为空的 Document 丢弃（如扫描件空白页）。
"""

import re

from langchain_core.documents import Document

# 零宽字符与 BOM
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
# 控制字符（保留 \n 与 \t）
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 页码行："- 3 -" / "— 12 —" / "第 5 页" / "第 5 页 共 20 页" / "Page 3" / "3/20"
_PAGE_NUMBER_RE = re.compile(
    r"""^\s*(?:
        [-—––]?\s*\d{1,4}\s*[-—––]?
        |第\s*\d{1,4}\s*页(?:\s*共\s*\d{1,4}\s*页)?
        |[Pp]age\s+\d{1,4}(?:\s*/\s*\d{1,4})?
        |\d{1,4}\s*/\s*\d{1,4}
    )\s*$""",
    re.VERBOSE,
)
# 纯符号装饰行（如 "----"、"****"、"===="）
_SYMBOL_LINE_RE = re.compile(r"^\s*[-—–=*_#~·.\s]{4,}$")

# 重复行判定：出现在至少 N 页的边缘区域且长度不超过 M，视为页眉/页脚
_REPEAT_MIN_PAGES = 3
_EDGE_LINE_MAX_LEN = 50
_EDGE_LINES = 2  # 每页首尾各检查的行数


def _is_noise_line(line: str) -> bool:
    """页码/纯符号装饰行（仅在页面边缘区域应用）。"""
    stripped = line.strip()
    return bool(stripped) and (
        bool(_PAGE_NUMBER_RE.match(stripped)) or bool(_SYMBOL_LINE_RE.match(stripped))
    )


def normalize_text(text: str) -> str:
    """字符级规范化：不改变换行结构，只清不可见噪音。"""
    if not text:
        return text
    text = text.replace("\u3000", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def find_repeated_edge_lines(pages_lines: list[list[str]]) -> set[str]:
    """识别跨页重复的页眉/页脚行文本，返回行文本集合。

    输入为每页的行文本列表（行级清洗场景使用，如 PDF 结构化解析）；
    每页仅前/后 _EDGE_LINES 行参与统计，且按页去重（短页面首尾切片
    重叠会把同一行计两次，导致 2 页重复即被误判）。
    """
    counts: dict[str, int] = {}
    for lines in pages_lines:
        stripped = [line.strip() for line in lines if line.strip()]
        edge_lines = set(stripped[:_EDGE_LINES] + stripped[-_EDGE_LINES:])
        for line in edge_lines:
            if len(line) <= _EDGE_LINE_MAX_LEN:
                counts[line] = counts.get(line, 0) + 1
    return {line for line, cnt in counts.items() if cnt >= _REPEAT_MIN_PAGES}


def _strip_headers_footers(pages: list[str]) -> list[str]:
    """识别并剔除跨页重复的页眉/页脚 + 边缘页码行。

    仅边缘区域的行参与剔除，正文永不改动。
    """
    texts = [normalize_text(p) for p in pages]
    repeated = find_repeated_edge_lines([[line for line in text.split("\n")] for text in texts])

    cleaned: list[str] = []
    for text in texts:
        lines = text.split("\n")
        kept: list[str] = []
        for i, line in enumerate(lines):
            at_edge = i < _EDGE_LINES or i >= len(lines) - _EDGE_LINES
            if at_edge and (line.strip() in repeated or _is_noise_line(line)):
                continue
            kept.append(line)
        cleaned.append("\n".join(kept))
    return cleaned


def clean_documents(docs: list[Document], ext: str) -> list[Document]:
    """清洗入口：按格式分发。PDF 做页眉页脚/页码清洗，其余做字符规范化。"""
    if not docs:
        return docs
    if ext == "pdf":
        cleaned = _strip_headers_footers([normalize_text(d.page_content or "") for d in docs])
        return [
            Document(page_content=text, metadata=dict(docs[i].metadata or {}))
            for i, text in enumerate(cleaned)
            if text.strip()
        ]
    result: list[Document] = []
    for doc in docs:
        text = normalize_text(doc.page_content or "")
        if text.strip():
            result.append(Document(page_content=text, metadata=dict(doc.metadata or {})))
    return result
