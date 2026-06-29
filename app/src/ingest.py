"""File -> markdown (markitdown) -> chunks -> embed -> Qdrant + Mongo."""
import base64
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from langchain_core.messages import HumanMessage
from markitdown import MarkItDown
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client.models import PointStruct

from . import config, stores, embeddings
from .gemini_wrapper import GeminiOpenAIWrapper

# Instantiate MarkItDown with OCR/Vision plugins enabled using gemini-3.1-flash-lite
_gemini_wrapper = GeminiOpenAIWrapper(config.llm())
_md = MarkItDown(
    enable_plugins=True,
    llm_client=_gemini_wrapper,
    llm_model=config.LLM_MODEL
)
_splitter = RecursiveCharacterTextSplitter(
    chunk_size=config.CHUNK_SIZE, chunk_overlap=config.CHUNK_OVERLAP
)

def describe_image(image_path: str) -> str:
    """Invokes Gemini Multimodal to describe raw image contents and perform OCR."""
    path = Path(image_path)
    suffix = path.suffix.lower()
    
    # Determine MIME type
    mime_type = "image/png"
    if suffix in (".jpg", ".jpeg"):
        mime_type = "image/jpeg"
    elif suffix == ".webp":
        mime_type = "image/webp"
    elif suffix == ".gif":
        mime_type = "image/gif"
        
    with open(image_path, "rb") as f:
        img_bytes = f.read()
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")
    
    prompt = (
        "You are an expert visual analyzer and OCR engine. Describe this image in detail for a RAG search database.\n\n"
        "Ensure you include:\n"
        "1. IMAGE TYPE: (e.g., Architecture Diagram, Flowchart, Plot/Chart, UI Screenshot, Illustration, Photo).\n"
        "2. MAIN TOPIC: A concise summary of what this image shows.\n"
        "3. DETAILED CONTENT: Describe all visual components, connections, boxes, arrows, flows, and relationships.\n"
        "4. COMPLETE OCR: Extract all text visible in the image. For tables or charts, represent them as Markdown tables.\n"
        "Format the output clearly using Markdown headings."
    )
    
    message = HumanMessage(
        content=[
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{img_b64}"},
            },
        ]
    )
    
    response = config.llm().invoke([message])
    content = response.content
    if isinstance(content, list):
        content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
    return content

def parse_svg(svg_path: str) -> str:
    """Parses SVG XML directly to extract text elements."""
    try:
        tree = ET.parse(svg_path)
        root = tree.getroot()
        
        text_elements = []
        # Find all text and tspan tags
        for elem in root.findall('.//{http://www.w3.org/2000/svg}text') + root.findall('.//text'):
            if elem.text:
                text_elements.append(elem.text.strip())
            for child in elem:
                if child.text:
                    text_elements.append(child.text.strip())
                    
        # Find title and desc
        for tag in ['title', 'desc']:
            for elem in root.findall(f'.//{{http://www.w3.org/2000/svg}}{tag}') + root.findall(f'.//{tag}'):
                if elem.text:
                    text_elements.append(f"{tag.capitalize()}: {elem.text.strip()}")
                    
        unique_texts = list(dict.fromkeys(filter(None, text_elements)))
        return "\n".join(unique_texts)
    except Exception as e:
        print(f"Error parsing XML from SVG {svg_path}: {e}")
        return ""



def _is_sep(line: str) -> bool:
    """Markdown table separator row, e.g. | --- | --- |."""
    s = line.strip()
    return s.startswith("|") and set(s) <= set("|-: ") and "-" in s


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _clean_header(cells: list[str]) -> bool:
    """A header safe to linearize: every column named, no blanks/NaN/Unnamed.
    PDF/xlsx tables fail this (empty cells, 'Unnamed: 1', 'NaN') -> left as-is."""
    return bool(cells) and all(
        c and c.lower() != "nan" and not c.lower().startswith("unnamed") for c in cells
    )


def _linearize_tables(markdown: str) -> str:
    """Clean markdown tables -> one self-describing line per row:
    '| BUG-004 | ... | Alice Nguyen | David Pham |' becomes
    'Bug ID: BUG-004; Reported By: Alice Nguyen; Assigned To: David Pham'.
    Kills the column-counting the judge/agent got wrong (reported-by vs assigned-to).
    Only touches clean tables; messy PDF/xlsx tables pass through untouched."""
    lines = markdown.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        hdr = _cells(lines[i]) if lines[i].strip().startswith("|") else None
        is_table = (
            hdr and i + 1 < len(lines) and _is_sep(lines[i + 1]) and _clean_header(hdr)
        )
        if not is_table:
            out.append(lines[i]); i += 1; continue
        i += 2  # skip header + separator (clean table rows are linearized and self-describing, so keeping header causes semantic dilution)
        while i < len(lines) and lines[i].strip().startswith("|") and not _is_sep(lines[i]):
            row = _cells(lines[i])
            if len(row) == len(hdr):
                out.append("; ".join(f"{h}: {v}" for h, v in zip(hdr, row) if v))
            else:
                out.append(lines[i])  # ragged row -> leave verbatim
            i += 1
    return "\n".join(out)


def _row_headers(markdown: str) -> dict[str, str]:
    """Map each table data-row -> its header block (header line + separator).
    A header is the line right before a separator row. Lets us re-attach headers
    to chunks where the splitter tore the data rows away from the column names."""
    lines = markdown.splitlines()
    mapping: dict[str, str] = {}
    header = None
    for i, line in enumerate(lines):
        if _is_sep(line) and i > 0:
            header = f"{lines[i-1].strip()}\n{line.strip()}"
            continue
        if not line.strip().startswith("|"):
            header = None
            continue
        if header and line.strip().startswith("|") and not _is_sep(line):
            mapping[line.strip()] = header
    return mapping


def _restore_headers(chunk: str, row2hdr: dict[str, str]) -> str:
    """If a chunk has table rows but no separator (header was split off),
    prepend the header of the table those rows belong to."""
    lines = [l.strip() for l in chunk.splitlines()]
    if any(_is_sep(l) for l in lines):
        return chunk  # header already present
    for l in lines:
        if l in row2hdr:
            return f"{row2hdr[l]}\n{chunk}"
    return chunk


import re
from pathlib import Path

def _find_nearest_header(markdown: str, chunk: str, start_idx: int = 0) -> tuple[str | None, int]:
    """Find the nearest markdown heading preceding the chunk's content."""
    idx = markdown.find(chunk, start_idx)
    if idx == -1:
        idx = markdown.find(chunk)  # fallback
        if idx == -1:
            return None, start_idx
    text_before = markdown[:idx]
    lines = text_before.splitlines()
    for line in reversed(lines):
        line_s = line.strip()
        if re.match(r"^#{1,6}\s+\S", line_s) or re.match(r"^\d+(\.\d+)*\.\s+\w", line_s):
            return line_s, idx
    return None, idx


def ingest(path: str, org_id: str | None = None) -> int:
    """Convert one file (document or image), store chunks. Returns chunks indexed."""
    org_id = org_id or config.ORG_ID
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    
    # Check if the file is an image
    is_image = suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")
    
    if is_image:
        markdown = ""
        # 1. Handle SVG text extraction first
        if suffix == ".svg":
            svg_text = parse_svg(path)
            if svg_text:
                markdown += f"### Raw Text Content from Vector SVG:\n{svg_text}\n\n"
            
            # Optional SVG to PNG conversion for multimodal vision description
            try:
                import cairosvg
                temp_png = file_path.with_suffix(".temp.png")
                cairosvg.svg2png(url=str(file_path), write_to=str(temp_png))
                visual_desc = describe_image(str(temp_png))
                markdown += f"### Visual Layout & Description:\n{visual_desc}"
                temp_png.unlink()  # Clean up
            except Exception as e:
                print(f"Skipping SVG visual analysis: cairosvg not working/installed ({e})")
                if not svg_text:
                    markdown = f"SVG file without extractable text or render support: {file_path.name}"
        else:
            # 2. Standard raster image visual description
            markdown = describe_image(path)
            
    else:
        # Standard document extraction using MarkItDown (which now handles embedded image OCR)
        markdown = _md.convert(path).text_content
        
    if not markdown.strip():
        return 0

    stores.save_doc(path, markdown, org_id)
    markdown = _linearize_tables(markdown)  # clean tables -> self-describing rows
    row2hdr = _row_headers(markdown)
    raw_chunks = _splitter.split_text(markdown)
    
    chunks = []
    start_idx = 0
    for c in raw_chunks:
        c_restored = _restore_headers(c, row2hdr)
        
        # Build contextual prefix
        hdr, match_idx = _find_nearest_header(markdown, c, start_idx)
        if match_idx != -1:
            start_idx = match_idx + len(c)
            
        context_prefix = f"Document: {file_path.name}"
        if hdr and hdr not in c:
            header_text = hdr.lstrip("# ").strip()
            context_prefix += f" | Section: {header_text}"
            
        chunks.append(f"{context_prefix}\n\n{c_restored}")

    vectors = embeddings.embed_docs(chunks)
    stores.ensure_collection(dim=len(vectors[0]))

    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=vec,
            payload={"text": chunk, "source": path, "org_id": org_id},
        )
        for chunk, vec in zip(chunks, vectors)
    ]
    stores.upsert(points)
    return len(points)


if __name__ == "__main__":
    # Self-check: python -m src.ingest <file>
    if len(sys.argv) < 2:
        sys.exit("usage: python -m src.ingest <file>")
    n = ingest(sys.argv[1])
    count = config.qdrant().count(config.COLLECTION).count
    print(f"indexed {n} chunks; qdrant total = {count}")
    assert n > 0, "no chunks indexed"
    assert count >= n, "qdrant count < indexed"
    print("OK")
