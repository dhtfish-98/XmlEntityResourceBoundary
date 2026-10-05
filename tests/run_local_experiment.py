"""Run an owned weak-baseline/fixed-parser comparison and save a receipt."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sys
from unittest.mock import patch

from xml_entity_resource_boundary import (
    ExternalResourceDenied,
    XmlBudgetExceeded,
    XmlPolicy,
    parse_xml,
)

from lab_support import external_document, nested_document, weak_parse_owned


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def run(output: Path) -> dict[str, object]:
    lab_dir = output.parent / ("owned-lab-" + output.stem)
    lab_dir.mkdir(parents=True, exist_ok=True)
    marker_file = lab_dir / "synthetic-external-entity.txt"
    marker = ("LAB_ONLY_" + secrets.token_hex(16)).encode("ascii")
    marker_file.write_bytes(marker)

    external_xml = external_document(marker_file.resolve().as_uri())
    nested_xml = nested_document(7)
    normal_xml = b'<root><item id="7">safe &amp; sound</item></root>'
    external_baseline = weak_parse_owned(external_xml, allowed_file=marker_file.resolve())
    nested_baseline = weak_parse_owned(nested_xml)

    external_fixed_error = "unexpected success"
    with patch.object(Path, "read_bytes", side_effect=AssertionError("fixed parser read a file")) as blocked_read:
        try:
            parse_xml(external_xml)
        except ExternalResourceDenied:
            external_fixed_error = "ExternalResourceDenied"
        fixed_file_reads = blocked_read.call_count

    nested_fixed_error = "unexpected success"
    try:
        parse_xml(nested_xml, policy=XmlPolicy(max_entity_expansion_bytes=128))
    except XmlBudgetExceeded:
        nested_fixed_error = "XmlBudgetExceeded"

    normal = parse_xml(normal_xml)
    normal_child = normal.root.children[0]
    normal_shape = {
        "root": normal.root.name,
        "child": normal_child.name,
        "child_attributes": list(normal_child.attributes),
        "child_text_sha256": _digest(normal_child.text_content().encode("utf-8")),
        "elements": normal.elements,
    }

    checks = {
        "baseline_owned_file_was_read_once": external_baseline.owned_file_reads == 1,
        "baseline_synthetic_file_content_expanded": marker.decode("ascii") in external_baseline.text,
        "fixed_external_resource_denied": external_fixed_error == "ExternalResourceDenied",
        "fixed_external_resource_file_reads_zero": fixed_file_reads == 0,
        "baseline_nested_entity_expanded": nested_baseline.text == "ha" * 128,
        "fixed_nested_entity_over_budget_denied": nested_fixed_error == "XmlBudgetExceeded",
        "normal_structure_preserved": (
            normal_shape["root"] == "root"
            and normal_shape["child"] == "item"
            and normal_child.attributes == (("id", "7"),)
            and normal_child.text_content() == "safe & sound"
            and normal.elements == 2
        ),
    }
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "version": "0.1.0",
        "environment": "owned local marker file and Python Expat parser; no network",
        "inputs": {
            "external_xml_sha256": _digest(external_xml),
            "nested_xml_sha256": _digest(nested_xml),
            "normal_xml_sha256": _digest(normal_xml),
            "owned_marker_sha256": _digest(marker),
            "owned_marker_path": str(marker_file),
            "marker_value": "synthetic value omitted from receipt",
        },
        "weak_baseline": {
            "owned_file_reads": external_baseline.owned_file_reads,
            "external_text_contains_marker": marker.decode("ascii") in external_baseline.text,
            "nested_output_bytes": len(nested_baseline.text.encode("utf-8")),
        },
        "fixed_parser": {
            "external_error": external_fixed_error,
            "owned_file_reads": fixed_file_reads,
            "nested_error": nested_fixed_error,
            "normal_structure": normal_shape,
        },
        "checks": checks,
        "result": "PASS" if all(checks.values()) else "FAIL",
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_local_experiment.py OUTPUT_JSON")
    destination = Path(sys.argv[1]).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    receipt = run(destination)
    destination.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(receipt["result"], destination)
    raise SystemExit(0 if receipt["result"] == "PASS" else 1)
