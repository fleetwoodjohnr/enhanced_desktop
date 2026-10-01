"""Turn a file into what a model can read: bounded text, or an image.

Detection is by content first (image signatures, zip containers, PDF magic)
and only then by name, because an extension is a claim. Everything here is
bounded -- time, bytes and characters -- since a file is untrusted input.
"""
from __future__ import annotations

import base64
import csv
import html
import io
import json
import re
import shutil
import subprocess
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

MAX_TEXT = 20_000
MAX_IMAGE_SIDE = 1600
PDF_PAGES = 60
PDF_TIMEOUT = 20

IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
# Formats a model cannot take directly but GdkPixbuf can decode.
CONVERTIBLE_IMAGES = (
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)
OFFICE = {
    "word/document.xml": "docx", "ppt/presentation.xml": "pptx", "xl/workbook.xml": "xlsx",
}
ODF = {"application/vnd.oasis.opendocument.text": "odt",
       "application/vnd.oasis.opendocument.spreadsheet": "ods",
       "application/vnd.oasis.opendocument.presentation": "odp"}
KIND_NAMES = {"text": "Text", "pdf": "PDF", "docx": "Word document", "odt": "Document",
              "pptx": "Presentation", "odp": "Presentation", "xlsx": "Spreadsheet",
              "ods": "Spreadsheet", "html": "Web page", "rtf": "Rich text", "csv": "Table",
              "json": "JSON", "image": "Image"}


class Unsupported(ValueError):
    """Neither text CLIVE can read nor an image."""


def image_type(data: bytes) -> str:
    for signature, kind in IMAGE_SIGNATURES:
        if data.startswith(signature):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def convertible_image(data: bytes, name: str) -> str:
    for signature, kind in CONVERTIBLE_IMAGES:
        if data.startswith(signature):
            return kind
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypavif") or \
            name.lower().endswith((".heic", ".heif", ".avif")):
        return "image/heif"
    return ""


def _pixbuf_png(data: bytes, max_side: int = MAX_IMAGE_SIDE) -> tuple[bytes, int, int]:
    """Decode with GdkPixbuf, shrink to max_side, and re-encode as PNG."""
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf, GLib
    loader = GdkPixbuf.PixbufLoader()
    try:
        loader.write(data)
        loader.close()
    except GLib.Error:
        raise Unsupported("this image format could not be decoded") from None
    pixbuf = loader.get_pixbuf()
    width, height = pixbuf.get_width(), pixbuf.get_height()
    scale = min(1.0, max_side / max(width, height))
    if scale < 1:
        pixbuf = pixbuf.scale_simple(max(1, round(width * scale)), max(1, round(height * scale)),
                                     GdkPixbuf.InterpType.BILINEAR)
    ok, png = pixbuf.save_to_bufferv("png", [], [])
    if not ok:
        raise Unsupported("this image could not be prepared")
    return bytes(png), pixbuf.get_width(), pixbuf.get_height()


def prepare_image(data: bytes, kind: str) -> tuple[str, str, int, int]:
    """(base64, mime, width, height), shrunk if large; native formats kept when small."""
    try:
        png, width, height = _pixbuf_png(data)
    except (ImportError, ValueError):
        if kind in ("image/png", "image/jpeg", "image/gif", "image/webp"):
            return base64.b64encode(data).decode("ascii"), kind, 0, 0
        raise
    if kind in ("image/png", "image/jpeg", "image/webp", "image/gif") and len(data) <= len(png) and \
            max(width, height) < MAX_IMAGE_SIDE:
        return base64.b64encode(data).decode("ascii"), kind, width, height
    return base64.b64encode(png).decode("ascii"), "image/png", width, height


def decode_text(data: bytes) -> str:
    """UTF-8 (with or without BOM), UTF-16 with a BOM, else Latin-1 for text-like bytes."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    # NUL is valid UTF-8, but no text file has one: an executable or archive
    # otherwise "decodes" into noise the model would be handed as text.
    if b"\x00" in data[:8192]:
        raise Unsupported("this file is not text CLIVE can read")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    sample = data[:8192]
    # A NUL byte, or many control characters, means binary rather than a
    # legacy-encoded text file.
    controls = sum(1 for byte in sample if byte < 32 and byte not in (9, 10, 12, 13))
    if b"\x00" in sample or controls > len(sample) * 0.05:
        raise Unsupported("this file is not text CLIVE can read")
    return data.decode("cp1252", errors="replace")


class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article"}

    def __init__(self):
        super().__init__()
        self.chunks, self.skip = [], 0

    def handle_starttag(self, tag, _attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.chunks.append(data)


def html_text(markup: str) -> str:
    parser = _HTMLText()
    parser.feed(markup)
    return re.sub(r"\n\s*\n+", "\n\n", html.unescape("".join(parser.chunks))).strip()


def rtf_text(source: str) -> str:
    """Plain text from RTF: good enough for letters and notes, not for tables."""
    text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode("cp1252"), source)
    text = re.sub(r"\\(par|line)\b ?", "\n", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    text = re.sub(r"[{}]", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _xml_text(data: bytes, paragraph_tags: tuple[str, ...]) -> str:
    """Text of an OOXML/ODF part, one paragraph per line."""
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return ""
    lines = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1]
        if tag in paragraph_tags:
            text = "".join(element.itertext()).strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


def _ooxml(archive: zipfile.ZipFile, kind: str) -> str:
    if kind == "docx":
        return _xml_text(archive.read("word/document.xml"), ("p",))
    if kind == "pptx":
        slides = sorted((n for n in archive.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                        key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        return "\n\n".join(f"Slide {i + 1}\n" + _xml_text(archive.read(name), ("p",))
                           for i, name in enumerate(slides))
    # xlsx: shared strings plus each sheet's cell values, as tab-separated rows.
    shared = []
    if "xl/sharedStrings.xml" in archive.namelist():
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        shared = ["".join(si.itertext()) for si in root if si.tag.endswith("si")]
    sheets = sorted(n for n in archive.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    parts = []
    for index, name in enumerate(sheets):
        root = ElementTree.fromstring(archive.read(name))
        rows = []
        for row in (e for e in root.iter() if e.tag.endswith("}row")):
            cells = []
            for cell in (c for c in row if c.tag.endswith("}c")):
                value = next((v.text or "" for v in cell if v.tag.endswith("}v")), "")
                if cell.get("t") == "s" and value.isdigit() and int(value) < len(shared):
                    value = shared[int(value)]
                elif cell.get("t") == "inlineStr":
                    value = "".join(cell.itertext())
                cells.append(value)
            rows.append("\t".join(cells))
        parts.append(f"Sheet {index + 1}\n" + "\n".join(rows))
    return "\n\n".join(parts)


def _odf(archive: zipfile.ZipFile) -> str:
    return _xml_text(archive.read("content.xml"), ("p", "h"))


def pdf_text(path: Path) -> tuple[str, int | None]:
    """pdftotext if installed (layout-aware, fast), else pypdf if present."""
    if shutil.which("pdftotext"):
        try:
            done = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", "-l", str(PDF_PAGES),
                                   str(path), "-"], capture_output=True, timeout=PDF_TIMEOUT, check=False)
        except subprocess.TimeoutExpired:
            raise Unsupported("this PDF took too long to read") from None
        if done.returncode == 0:
            text = done.stdout.decode("utf-8", errors="replace")
            return text, text.count("\f") or None
        raise Unsupported("this PDF could not be read (it may be encrypted)")
    try:
        from pypdf import PdfReader
    except ImportError:
        raise Unsupported("reading PDFs needs pdftotext (the poppler-utils package)") from None
    reader = PdfReader(str(path))
    pages = reader.pages[:PDF_PAGES]
    return "\n\f".join(page.extract_text() or "" for page in pages), len(reader.pages)


def extract(path: Path, data: bytes) -> dict:
    """The model-ready form of one file's bytes: kind, and text or an image."""
    name = path.name
    kind = image_type(data)
    if kind:
        encoded, mime, width, height = prepare_image(data, kind)
        return {"kind": "image", "type": mime, "image": encoded, "width": width, "height": height,
                "label": KIND_NAMES["image"]}
    kind = convertible_image(data, name)
    if kind:
        png, width, height = _pixbuf_png(data)
        return {"kind": "image", "type": "image/png", "image": base64.b64encode(png).decode("ascii"),
                "width": width, "height": height, "label": KIND_NAMES["image"]}

    pages = None
    if data.startswith(b"%PDF-"):
        text, pages = pdf_text(path)
        label = "pdf"
    elif data.startswith(b"PK\x03\x04"):
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
            names = set(archive.namelist())
            office = next((k for member, k in OFFICE.items() if member in names), "")
            mimetype = archive.read("mimetype").decode(errors="ignore").strip() if "mimetype" in names else ""
            if office:
                text, label = _ooxml(archive, office), office
            elif mimetype in ODF:
                text, label = _odf(archive), ODF[mimetype]
            else:
                raise Unsupported("this archive is not a document CLIVE can read")
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
            raise Unsupported("this document is damaged or not a supported format") from None
    else:
        text = decode_text(data)
        lowered = name.lower()
        if lowered.endswith((".html", ".htm", ".xhtml")) or text.lstrip()[:15].lower().startswith(("<!doctype html", "<html")):
            text, label = html_text(text), "html"
        elif lowered.endswith(".rtf") or text.startswith("{\\rtf"):
            text, label = rtf_text(text), "rtf"
        elif lowered.endswith((".csv", ".tsv")):
            label = "csv"
            text = _tidy_csv(text, "\t" if lowered.endswith(".tsv") else ",")
        elif lowered.endswith(".json"):
            label = "json"
            try:
                text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
            except ValueError:
                pass
        else:
            label = "text"
    return {"kind": "text", "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT,
            "chars": len(text), "pages": pages, "label": KIND_NAMES.get(label, "Text")}


def _tidy_csv(text: str, delimiter: str) -> str:
    """Rows as tab-separated lines, so the model sees columns, not quoting."""
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except csv.Error:
        return text
    return "\n".join("\t".join(row) for row in rows)
