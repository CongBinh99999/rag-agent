"""File -> markdown (markitdown) -> chunks -> embed -> Qdrant + Mongo."""
import sys
import uuid

from markitdown import MarkItDown
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client.models import PointStruct

from . import config, stores, embeddings

_md = MarkItDown(enable_plugins=False)
_splitter = RecursiveCharacterTextSplitter(
    chunk_size=config.CHUNK_SIZE, chunk_overlap=config.CHUNK_OVERLAP
)


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
        out.append(lines[i])  # keep header (search still benefits from column names)
        i += 2  # skip header + separator
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


def ingest(path: str, org_id: str | None = None) -> int:
    """Convert one file, store chunks. Returns number of chunks indexed."""
    org_id = org_id or config.ORG_ID
    markdown = _md.convert(path).text_content
    if not markdown.strip():
        return 0

    stores.save_doc(path, markdown, org_id)
    markdown = _linearize_tables(markdown)  # clean tables -> self-describing rows
    row2hdr = _row_headers(markdown)
    chunks = [_restore_headers(c, row2hdr) for c in _splitter.split_text(markdown)]
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
