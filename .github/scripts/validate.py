"""Validate exact source, owned experiment, built packages, and installed use."""

from __future__ import annotations

from datetime import datetime, timezone
from email.parser import Parser
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile
from xml.parsers import expat


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT
DOCUMENTS = ROOT / "项目文档"
BUILD = ROOT / "Build"
SOURCE_FILES = (
    ".gitignore",
    "pyproject.toml",
    "src/xml_entity_resource_boundary/__init__.py",
    "src/xml_entity_resource_boundary/parser.py",
    "tests/lab_support.py",
    "tests/run_local_experiment.py",
    "tests/test_xml_boundary.py",
)
VERSION = "0.1.1"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    return digest(path.read_bytes())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_command(label: str, argv: list[str], cwd: Path, env: dict[str, str]) -> dict[str, object]:
    log = BUILD / (label + ".log")
    try:
        completed = subprocess.run(
            argv, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=180, check=False,
        )
        output = completed.stdout
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or b"")
        if isinstance(output, bytes):
            output = output.decode("utf-8", "replace")
        output += "\nvalidation command timed out after 180 seconds\n"
        exit_code = 124
    log.write_text(output)
    return {
        "label": label,
        "argv": argv,
        "cwd": str(cwd),
        "exit_code": exit_code,
        "log_path": str(log),
        "log_sha256": file_digest(log),
    }


def archive_checks(wheel: Path, sdist: Path) -> tuple[dict[str, bool], dict[str, object]]:
    checks: dict[str, bool] = {}
    artifacts: dict[str, object] = {}
    with zipfile.ZipFile(wheel) as archive:
        wheel_names = sorted(archive.namelist())
        wheel_prefix = f"xml_entity_resource_boundary-{VERSION}.dist-info/"
        checks["wheel_only_expected_entries"] = set(wheel_names) == {
            "xml_entity_resource_boundary/__init__.py",
            "xml_entity_resource_boundary/parser.py",
            wheel_prefix + "METADATA",
            wheel_prefix + "RECORD",
            wheel_prefix + "WHEEL",
            wheel_prefix + "licenses/LICENSE",
        }
        metadata_name = next(name for name in wheel_names if name.endswith("/METADATA"))
        license_name = next(name for name in wheel_names if name.endswith("/licenses/LICENSE"))
        metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
        checks["wheel_runtime_and_license_match_current_files"] = (
            archive.read("xml_entity_resource_boundary/parser.py")
            == (SOURCE / "src/xml_entity_resource_boundary/parser.py").read_bytes()
            and archive.read("xml_entity_resource_boundary/__init__.py")
            == (SOURCE / "src/xml_entity_resource_boundary/__init__.py").read_bytes()
            and archive.read(license_name) == (DOCUMENTS / "LICENSE").read_bytes()
        )
        checks["wheel_metadata_author_version_license"] = (
            metadata.get("Name") == "xml-entity-resource-boundary"
            and metadata.get("Version") == VERSION
            and metadata.get("Author") == "dhtfish98"
            and metadata.get("License-Expression") == "MIT"
        )
        artifacts["wheel"] = {
            "path": str(wheel), "sha256": file_digest(wheel), "entries": wheel_names,
        }
    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = sorted(archive.getnames())
        prefix = f"xml_entity_resource_boundary-{VERSION}/"
        checks["sdist_only_expected_entries"] = set(sdist_names) == {
            prefix + name for name in (*SOURCE_FILES, "LICENSE", "PKG-INFO")
        }
        expected = {name: (SOURCE / name).read_bytes() for name in SOURCE_FILES}
        checks["sdist_sources_tests_license_match_current_files"] = all(
            archive.extractfile(prefix + name).read() == value
            for name, value in expected.items()
        ) and archive.extractfile(prefix + "LICENSE").read() == (DOCUMENTS / "LICENSE").read_bytes()
        sdist_metadata = Parser().parsestr(
            archive.extractfile(prefix + "PKG-INFO").read().decode("utf-8")
        )
        checks["sdist_metadata_author_version_license"] = (
            sdist_metadata.get("Name") == "xml-entity-resource-boundary"
            and sdist_metadata.get("Version") == VERSION
            and sdist_metadata.get("Author") == "dhtfish98"
            and sdist_metadata.get("License-Expression") == "MIT"
        )
        artifacts["sdist"] = {
            "path": str(sdist), "sha256": file_digest(sdist), "entries": sdist_names,
        }
    return checks, artifacts


def main() -> int:
    BUILD.mkdir(parents=True, exist_ok=True)
    receipt: dict[str, object] = {
        "started_at_utc": utc_now(),
        "project": "XmlEntityResourceBoundary",
        "version": VERSION,
        "author": "dhtfish98",
        "python_runtime": sys.version.split()[0],
        "expat_runtime": expat.EXPAT_VERSION,
        "source_root": str(SOURCE),
        "documentation": str(DOCUMENTS / "README.md"),
        "license_document": str(DOCUMENTS / "LICENSE"),
        "upstream_snapshot": "libexpat/libexpat@ea81746aa19794418825657dc82121f2a6779143",
        "upstream_commit_topic": "Fil-C CI toolchain version update; not an XML security fix",
        "validator_sha256": file_digest(Path(__file__)),
        "commands": [],
        "checks": {},
        "status": "FAIL",
    }
    commands: list[dict[str, object]] = receipt["commands"]
    checks: dict[str, bool] = receipt["checks"]
    try:
        source_manifest = [
            {"path": name, "sha256": file_digest(SOURCE / name)}
            for name in SOURCE_FILES
        ]
        receipt["source_files"] = source_manifest
        receipt["source_manifest_sha256"] = digest(
            "".join(f"{item['path']} {item['sha256']}\n" for item in source_manifest).encode()
        )
        receipt["documentation_sha256"] = file_digest(DOCUMENTS / "README.md")
        receipt["license_document_sha256"] = file_digest(DOCUMENTS / "LICENSE")

        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["UV_CACHE_DIR"] = str(BUILD / "uv-cache")
        environment["XDG_CACHE_HOME"] = str(BUILD / "xdg-cache")
        environment["PIP_CACHE_DIR"] = str(BUILD / "pip-cache")
        environment["TMPDIR"] = str(BUILD / "tmp")
        (BUILD / "tmp").mkdir(exist_ok=True)

        source_env = environment.copy()
        source_env["PYTHONPATH"] = str(SOURCE / "src")
        source_tests = run_command(
            "source-tests",
            [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
            SOURCE,
            source_env,
        )
        commands.append(source_tests)
        checks["source-tests"] = source_tests["exit_code"] == 0
        if not checks["source-tests"]:
            return 1

        source_lab_path = BUILD / "lab-source.json"
        source_lab = run_command(
            "source-lab",
            [sys.executable, "-B", str(SOURCE / "tests/run_local_experiment.py"), str(source_lab_path)],
            SOURCE,
            source_env,
        )
        commands.append(source_lab)
        checks["source-lab"] = source_lab["exit_code"] == 0
        if not checks["source-lab"]:
            return 1

        stage = BUILD / "stage"
        if stage.exists():
            shutil.rmtree(stage)
        for name in SOURCE_FILES:
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SOURCE / name, destination)
        shutil.copy2(DOCUMENTS / "LICENSE", stage / "LICENSE")
        checks["staged_license_equals_document"] = (
            (stage / "LICENSE").read_bytes() == (DOCUMENTS / "LICENSE").read_bytes()
        )

        dist = BUILD / "dist"
        if dist.exists():
            shutil.rmtree(dist)
        dist.mkdir()
        build = run_command("build", ["uv", "build", "--out-dir", str(dist)], stage, environment)
        commands.append(build)
        checks["build"] = build["exit_code"] == 0
        if not checks["build"]:
            return 1
        wheels = list(dist.glob("*.whl"))
        sdists = list(dist.glob("*.tar.gz"))
        checks["one_wheel_one_sdist"] = (
            len(wheels) == 1 and len(sdists) == 1
            and wheels[0].name == f"xml_entity_resource_boundary-{VERSION}-py3-none-any.whl"
            and sdists[0].name == f"xml_entity_resource_boundary-{VERSION}.tar.gz"
        )
        if not checks["one_wheel_one_sdist"]:
            return 1
        archive_results, artifacts = archive_checks(wheels[0], sdists[0])
        checks.update(archive_results)
        receipt["artifacts"] = artifacts

        consumer = BUILD / "consumer"
        consumer.mkdir(exist_ok=True)
        venv = BUILD / "venv"
        venv_step = run_command(
            "venv", ["uv", "venv", "--clear", "--python", sys.executable, str(venv)],
            consumer, environment,
        )
        commands.append(venv_step)
        checks["venv"] = venv_step["exit_code"] == 0
        if not checks["venv"]:
            return 1
        installed_python = venv / "bin/python"
        install = run_command(
            "install-wheel",
            ["uv", "pip", "install", "--python", str(installed_python), "--no-deps", str(wheels[0])],
            consumer, environment,
        )
        commands.append(install)
        checks["install-wheel"] = install["exit_code"] == 0
        if not checks["install-wheel"]:
            return 1

        installed_env = environment.copy()
        installed_env.pop("PYTHONPATH", None)
        import_code = (
            "import xml_entity_resource_boundary as x; from pathlib import Path; "
            f"assert x.__version__ == '{VERSION}'; "
            f"assert Path(x.__file__).resolve().is_relative_to(Path({str(venv)!r}).resolve()); "
            "print(x.__version__, x.__file__)"
        )
        imported = run_command(
            "installed-import", [str(installed_python), "-B", "-c", import_code],
            consumer, installed_env,
        )
        commands.append(imported)
        checks["installed-import"] = imported["exit_code"] == 0
        if not checks["installed-import"]:
            return 1

        installed_tests = run_command(
            "installed-tests",
            [str(installed_python), "-B", "-m", "unittest", "discover", "-s", str(SOURCE / "tests"), "-v"],
            consumer, installed_env,
        )
        commands.append(installed_tests)
        checks["installed-tests"] = installed_tests["exit_code"] == 0
        if not checks["installed-tests"]:
            return 1

        installed_lab_path = BUILD / "lab-installed.json"
        installed_lab = run_command(
            "installed-lab",
            [str(installed_python), "-B", str(SOURCE / "tests/run_local_experiment.py"), str(installed_lab_path)],
            consumer, installed_env,
        )
        commands.append(installed_lab)
        checks["installed-lab"] = installed_lab["exit_code"] == 0
        if not checks["installed-lab"]:
            return 1

        source_receipt = json.loads(source_lab_path.read_text())
        installed_receipt = json.loads(installed_lab_path.read_text())
        checks["both_lab_receipts_pass"] = (
            source_receipt["result"] == "PASS" and installed_receipt["result"] == "PASS"
            and all(source_receipt["checks"].values())
            and all(installed_receipt["checks"].values())
        )
        checks["synthetic_marker_not_in_json_receipts"] = all(
            "LAB_ONLY_" not in path.read_text()
            for path in (source_lab_path, installed_lab_path)
        )
        checks["author_version_license"] = (
            "dhtfish98" in (DOCUMENTS / "LICENSE").read_text()
            and VERSION in (SOURCE / "src/xml_entity_resource_boundary/__init__.py").read_text()
        )
        checks["source_tree_only_expected_files"] = sorted(
            [".gitignore", "pyproject.toml"] + [
                str(path.relative_to(SOURCE))
                for base in (SOURCE / "src", SOURCE / "tests")
                for path in base.rglob("*")
                if path.is_file()
            ]
        ) == sorted(SOURCE_FILES)
        receipt["status"] = "PASS" if all(checks.values()) else "FAIL"
        return 0 if receipt["status"] == "PASS" else 1
    except Exception as error:
        receipt["fatal_error"] = f"{type(error).__name__}: {error}"
        return 1
    finally:
        receipt["finished_at_utc"] = utc_now()
        (BUILD / "validation.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        print(receipt["status"], BUILD / "validation.json")


if __name__ == "__main__":
    raise SystemExit(main())
