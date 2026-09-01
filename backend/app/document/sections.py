"""按标题分节聚合（docx / PDF 结构化解析共用）。

- 同一章节的文本行（段落、表格行）合并为一个 Document，换行连接保持行结构；
- 标题行进 chunk 内容（向量/BM25 检索可命中），title_path 记入 metadata；
- 连排标题（标题后暂无正文，如"第一章"后紧跟"第一条"）合并进下一章节，
  不产生孤立标题 chunk；
- 长章节由切分阶段递归切分，子 chunk 经 split_documents 继承 title_path。
"""

from langchain_core.documents import Document


class SectionAggregator:
    """累积标题/正文行，按章节落为 Document 列表。"""

    def __init__(self) -> None:
        self._docs: list[Document] = []
        self._headings: dict[int, str] = {}
        self._section: list[str] = []
        self._has_body = False

    def _title_path(self) -> str | None:
        path = " > ".join(
            self._headings[level] for level in sorted(self._headings) if self._headings[level]
        )
        return path or None

    def add_heading(self, text: str, level: int) -> None:
        """记录一个标题行：上一章节已有正文才落盘，标题文本进内容。"""
        if self._has_body:
            self.flush()
        self._headings = {l: t for l, t in self._headings.items() if l < level}
        self._headings[level] = text
        self._section.append(text)

    def add_body(self, text: str) -> None:
        """累积正文/表格行。"""
        self._section.append(text)
        self._has_body = True

    def flush(self) -> None:
        """把当前章节累积的行落为一个 Document（换行保持行结构）。"""
        if self._section:
            self._docs.append(
                Document(
                    page_content="\n".join(self._section),
                    metadata={"title_path": self._title_path()},
                )
            )
            self._section = []
        self._has_body = False

    @property
    def documents(self) -> list[Document]:
        return self._docs
