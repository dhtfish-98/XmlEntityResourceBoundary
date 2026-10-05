"""Owned, deliberately weak Expat baseline for synthetic comparisons only."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.parsers import expat


@dataclass(frozen=True)
class WeakOutcome:
    text: str
    owned_file_reads: int


def nested_document(levels: int = 7) -> bytes:
    if levels < 0 or levels > 10:
        raise ValueError("synthetic level count must be within 0..10")
    declarations = ['<!ENTITY e0 "ha">']
    for level in range(1, levels + 1):
        previous = f"e{level - 1}"
        declarations.append(f'<!ENTITY e{level} "&{previous};&{previous};">')
    return (
        "<!DOCTYPE root [" + "".join(declarations) + f"]><root>&e{levels};</root>"
    ).encode("ascii")


def external_document(system_uri: str) -> bytes:
    if not system_uri.startswith("file://") or '"' in system_uri:
        raise ValueError("the weak experiment accepts one owned file URI only")
    return (
        '<!DOCTYPE root [<!ENTITY external SYSTEM "'
        + system_uri
        + '">]><root>&external;</root>'
    ).encode("ascii")


def weak_parse_owned(document: bytes, *, allowed_file: Path | None = None) -> WeakOutcome:
    """Unsafe by design, but its resolver can read only one supplied local file."""
    parser = expat.ParserCreate()
    chunks: list[str] = []
    file_reads = 0
    parser.CharacterDataHandler = chunks.append

    def on_external(context: str, _base: str | None, system_id: str, _public_id: str | None) -> int:
        nonlocal file_reads
        if allowed_file is None or system_id != allowed_file.as_uri():
            raise AssertionError("weak baseline attempted a resource outside its owned file")
        file_reads += 1
        entity_parser = parser.ExternalEntityParserCreate(context)
        entity_parser.CharacterDataHandler = chunks.append
        entity_parser.Parse(allowed_file.read_bytes(), True)
        return 1

    parser.ExternalEntityRefHandler = on_external
    parser.Parse(document, True)
    return WeakOutcome("".join(chunks), file_reads)
