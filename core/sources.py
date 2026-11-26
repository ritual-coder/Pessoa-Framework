"""Reading Prima Materia source files as plain text.

Supported: .md, .txt, .docx (standard library: a .docx is a zip of XML) and
.pdf (pypdf). Anything that cannot yield text raises ValueError with the
reason, so a scanned PDF or a mis-encoded file is reported, never silently
read as empty.
"""

import os
import zipfile
from xml.etree import ElementTree

SUPPORTED = (".md", ".txt", ".docx", ".pdf")

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _read_plain(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except UnicodeDecodeError:
        raise ValueError("not UTF-8 text; re-save the file as UTF-8") from None


def _read_docx(path):
    try:
        with zipfile.ZipFile(path) as z:
            root = ElementTree.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError):
        raise ValueError("not a valid .docx (no word/document.xml)") from None
    paragraphs = []
    for p in root.iter(f"{_W}p"):
        parts = []
        for node in p.iter():
            if node.tag == f"{_W}t":
                parts.append(node.text or "")
            elif node.tag == f"{_W}tab":
                parts.append("\t")
            elif node.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        paragraphs.append("".join(parts))
    return "\n".join(paragraphs)


def _read_pdf(path):
    from pypdf import PdfReader  # imported here so .md/.txt/.docx work without it

    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError("PDF is password-protected")
    text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    if not text.strip():
        raise ValueError("PDF has no extractable text (a scan? it needs OCR first)")
    return text


def read_source(path):
    """The text of one supported source file."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".md", ".txt"):
        return _read_plain(path)
    if ext == ".docx":
        return _read_docx(path)
    if ext == ".pdf":
        return _read_pdf(path)
    raise ValueError(f"unsupported file type {ext!r}; use one of {', '.join(SUPPORTED)}")
