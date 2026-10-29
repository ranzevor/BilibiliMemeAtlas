#!/usr/bin/env python3
"""Run structural, projection and behavioural acceptance checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True

from atlas import ROOT, CatalogueError, load_catalogue, stats
from build import projections
from validate import validate_all


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def result_value(outcome):
    return {
        "tests_run": outcome.testsRun,
        "failures": len(outcome.failures),
        "errors": len(outcome.errors),
        "skipped": len(outcome.skipped),
        "result": "PASS" if outcome.wasSuccessful() else "FAIL",
    }


def write_result(path, value):
    destination = Path(path)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8")
    os.replace(temporary, destination)


def run_module(module: str, result_file: str) -> int:
    sys.path.insert(0, str(ROOT / "tests"))
    names = module.split(",")
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    outcome = unittest.TextTestRunner(verbosity=2).run(suite)
    write_result(result_file, result_value(outcome))
    return 0 if outcome.wasSuccessful() else 1


def _result_name(identifier: str) -> str:
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:16]
    return f"{digest}.json"


def run_process(identifier: str, result_dir: Path, timeout: int = 240):
    result_file = result_dir / _result_name(identifier)
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        completed = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()),
             "--module", identifier, "--result-file", str(result_file)],
            cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
            timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"验收项超时: {identifier}", file=sys.stderr)
        return {"tests_run": 0, "failures": 0, "errors": 1,
                "skipped": 0, "result": "FAIL"}
    if not result_file.is_file():
        print(f"验收项未返回结果: {identifier}", file=sys.stderr)
        return {"tests_run": 0, "failures": 0, "errors": 1,
                "skipped": 0, "result": "FAIL"}
    try:
        value = json.loads(result_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        print(f"验收项结果无效: {identifier}: {exc}", file=sys.stderr)
        return {"tests_run": 0, "failures": 0, "errors": 1,
                "skipped": 0, "result": "FAIL"}
    if completed.returncode != 0 or value.get("result") != "PASS":
        value["result"] = "FAIL"
    return value


def test_ids(module: str):
    sys.path.insert(0, str(ROOT / "tests"))
    suite = unittest.defaultTestLoader.loadTestsFromName(module)
    return [test.id() for test in flatten(suite)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", help=argparse.SUPPRESS)
    parser.add_argument("--result-file", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.module:
        if not args.result_file:
            parser.error("--result-file is required with --module")
        return run_module(args.module, args.result_file)

    try:
        errors = validate_all()
        if not errors:
            for name, expected in projections().items():
                path = ROOT / name
                if not path.exists() or path.read_bytes() != expected.encode("utf-8"):
                    errors.append(f"派生文件不同步: {name}")
        for error in errors:
            print(error, file=sys.stderr)
        if errors:
            return 1

        modules = sorted(path.stem for path in (ROOT / "tests").glob("test_*.py"))
        summary = {"tests_run": 0, "failures": 0, "errors": 0, "skipped": 0}
        successful = True
        with tempfile.TemporaryDirectory(prefix="bma-verify-results-") as temporary:
            result_dir = Path(temporary)
            for module in modules:
                if module == "test_install":
                    identifiers = test_ids(module)
                    grouped = []
                    current_class = None
                    current = []
                    for identifier in identifiers:
                        test_class = identifier.rsplit(".", 1)[0]
                        if current and (test_class != current_class or len(current) == 4):
                            grouped.append(",".join(current))
                            current = []
                        current_class = test_class
                        current.append(identifier)
                    if current:
                        grouped.append(",".join(current))
                    values = [run_process(group, result_dir, 180)
                              for group in grouped]
                else:
                    values = [run_process(module, result_dir)]
                for value in values:
                    for key in summary:
                        summary[key] += int(value.get(key, 0))
                    if value.get("result") != "PASS":
                        successful = False

        entries, sources, metadata = load_catalogue()
        report = {
            **summary,
            "data": stats(entries, sources, metadata),
            "result": "PASS" if successful else "FAIL",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if successful else 1
    except (CatalogueError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"验收失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
