from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PyPdfError


def extract_uploaded_text(filename: str, content: bytes) -> str:
    if not content:
        raise ValueError("上传文件为空。")
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "pdf":
        try:
            reader = PdfReader(BytesIO(content))
            pages = []
            for page_number, page in enumerate(reader.pages, start=1):
                page_text = (page.extract_text() or "").strip()
                if page_text:
                    pages.append(f"[第 {page_number} 页]\n{page_text}")
            text = "\n\n".join(pages)
        except (PyPdfError, OSError, EOFError) as error:
            raise ValueError("PDF 文件损坏或无法解析，请重新上传有效文件。") from error
    elif suffix in {"txt", "md"}:
        text = content.decode("utf-8-sig")
    else:
        raise ValueError("当前仅支持 PDF、TXT 或 Markdown 文件。")
    if len(text.strip()) < 10:
        raise ValueError("未能从文件中提取到足够文本；扫描型 PDF 需要先进行 OCR。")
    return text.strip()
