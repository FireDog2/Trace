from __future__ import annotations

import io
import zipfile

from data_analysis_agent.knowledge_import import extract_knowledge_candidates
from reportlab.pdfgen.canvas import Canvas


def test_markdown_document_is_split_and_classified() -> None:
    content = """# 经营利润
类型：指标
定义：收入减去物流与运营成本。
公式：revenue - logistics_cost - operating_cost
适用维度：月份，地区

# 活跃客户
定义：过去 90 天内至少完成一笔订单的客户。
别名：Active customer

# 数据使用边界
定义：敏感字段必须经过审批，不得直接导出。
""".encode()

    candidates = extract_knowledge_candidates("财务口径.md", content)

    assert [candidate.name for candidate in candidates] == ["经营利润", "活跃客户", "数据使用边界"]
    assert [candidate.kind for candidate in candidates] == ["metric", "term", "rule"]
    assert candidates[0].dimensions == ("月份", "地区")
    assert candidates[1].synonyms == ("Active customer",)


def test_docx_document_text_is_read_without_external_word_dependency() -> None:
    document_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>经营利润</w:t></w:r></w:p>
<w:p><w:r><w:t>定义：收入减去全部经营成本。</w:t></w:r></w:p>
</w:body></w:document>'''.encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    candidates = extract_knowledge_candidates("财务口径.docx", buffer.getvalue())

    assert candidates[0].name == "经营利润"
    assert candidates[0].kind == "metric"


def test_pdf_document_text_is_available_to_knowledge_search() -> None:
    buffer = io.BytesIO()
    document = Canvas(buffer)
    document.drawString(72, 720, "Revenue metric means recognized sales before tax.")
    document.save()

    candidates = extract_knowledge_candidates("finance-catalog.pdf", buffer.getvalue())

    assert candidates[0].name == "finance-catalog"
    assert "recognized sales" in candidates[0].definition
    assert candidates[0].kind == "metric"
