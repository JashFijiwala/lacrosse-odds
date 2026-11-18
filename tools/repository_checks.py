"""Read-only collaboration inventory, integrity, link, credential and import checks."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
PREP = ROOT / "repo_prep/2026-11-18"
PATTERNS = {
    "credential_assignment": re.compile(
        r'''(?i)(?:["']?(?:ODDS_API_KEY|api[_-]?key|apiKey|access[_-]?token|password|client[_-]?secret)["']?\s*[:=]\s*["'])([A-Za-z0-9_+/=-]{16,})(?:["'])'''),
    "credential_url": re.compile(
        r"(?i)(?:https?://[^\s/@]+:[^\s/@]+@|[?&](?:apiKey|api_key|access_token)=[A-Za-z0-9_-]{16,})"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "provider_token": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})\b"),
    "authorization": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{16,}"),
}


def scan(data: bytes, path: str) -> list[dict]:
    """Return locations and types only; never include matched credential text."""
    text = data.decode("utf-8", errors="replace")
    return [{"path": path, "line": text.count("\n", 0, m.start())+1, "type": name}
            for name, pattern in PATTERNS.items() for m in pattern.finditer(text)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true", help="save preparation check evidence")
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    # No entry point is called. Import checks cannot contact the network.
    def no_network(*a, **k):
        raise RuntimeError("network blocked by repository checks")
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    socket.create_connection = no_network
    socket.getaddrinfo = no_network

    report = {"python": sys.version.split()[0], "errors": [], "secret_findings": [],
              "local_path_findings": [], "links_checked": 0, "immutable_present": 0,
              "immutable_absent": [], "immutable_changed": [], "candidate_bytes": 0}
    candidates = (PREP / "commit_files.txt").read_text(encoding="utf-8").splitlines()
    report["candidate_count"] = len(candidates)
    if len(candidates) != len(set(candidates)):
        report["errors"].append("duplicate commit inventory paths")
    inventory = {r["path"]: r for r in csv.DictReader(
        (PREP / "publication_inventory.csv").open(newline="", encoding="utf-8"))}
    for rel in candidates:
        path = ROOT / rel
        if inventory.get(rel, {}).get("publication_status") != "approved candidate for Git":
            report["errors"].append(f"not a reviewed candidate: {rel}")
        if not path.is_file():
            report["errors"].append(f"candidate absent: {rel}")
            continue
        # Refuse unsafe inventory entries rather than ever opening these inputs.
        if path.resolve().is_relative_to(ROOT) is False or rel.startswith(("pilot/raw/", "pilot/normalized/", "pilot/ledger/", "external/")) or (path.name.startswith(".env") and path.name != ".env.example"):
            report["errors"].append(f"restricted inventory path: {rel}")
            continue
        data = path.read_bytes()
        report["candidate_bytes"] += len(data)
        report["secret_findings"].extend(scan(data, rel))
        text = data.decode("utf-8", errors="replace")
        for match in re.finditer(r"(?:[A-Za-z]:[\\/]Users[\\/]|/Users/|/home/)[\w .-]+[\\/]", text):
            report["local_path_findings"].append({"path": rel, "line": text.count("\n", 0, match.start())+1})
        if path.suffix == ".md":
            for target in re.findall(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", text):
                target = unquote(target.strip().strip("<>"))
                if re.match(r"[a-z]+:", target, re.I) or target.startswith("#"):
                    continue
                file_part = target.split("#", 1)[0]
                if not file_part:
                    continue
                report["links_checked"] += 1
                destination = (path.parent / file_part).resolve()
                if not destination.exists():
                    report["errors"].append(f"missing local link in {rel}: {file_part}")
                elif destination.is_file() and destination.is_relative_to(ROOT):
                    dest_rel = destination.relative_to(ROOT).as_posix()
                    if dest_rel not in candidates:
                        report["errors"].append(f"link to withheld file in {rel}: {dest_rel}")
    for row in csv.DictReader((PREP / "immutable_before.csv").open(newline="", encoding="utf-8")):
        rel = row["path"]
        path = ROOT / rel
        if not path.is_file():
            report["immutable_absent"].append(rel)
            continue
        # Never read private operational state in the teammate checker.
        if rel.startswith("pilot/ledger/") or rel == "pilot/cradl_observations.csv":
            continue
        report["immutable_present"] += 1
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            report["immutable_changed"].append(rel)
    protected = (ROOT / "audit_join/protected_hashes.txt").read_text(encoding="utf-8").splitlines()
    report["protected_code_checked"] = len(protected)
    for line in protected:
        expected, rel = line.split()
        if hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() != expected:
            report["errors"].append(f"protected source hash mismatch: {rel}")

    deps = []
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        name, expected = line.split("==")
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            installed = "MISSING"
        deps.append({"name": name, "expected": expected, "installed": installed})
        if installed != expected:
            report["errors"].append(f"dependency version mismatch: {name}")
    report["dependencies"] = deps
    packages = ["requests", "pandas", "numpy", "pyarrow", "dotenv", "yaml", "matplotlib", "pytest",
                "src.ledger", "src.halt", "src.odds_client", "src.normalize", "src.analysis", "src.costs",
                "audit_join.join_audit", "audit_join.join_revision", "elo_feas.data_layer",
                "elo_feas.elo", "elo_feas.metrics", "elo_feas.audit_elo"]
    report["imports_ok"] = []
    with tempfile.TemporaryDirectory(prefix="lacrosse-import-check-") as tmp:
        previous = os.environ.get("MPLCONFIGDIR")
        os.environ["MPLCONFIGDIR"] = tmp
        try:
            for name in packages:
                try:
                    importlib.import_module(name)
                    report["imports_ok"].append(name)
                except Exception as exc:
                    report["errors"].append(f"import failed: {name} ({type(exc).__name__})")
        finally:
            if previous is None:
                os.environ.pop("MPLCONFIGDIR", None)
            else:
                os.environ["MPLCONFIGDIR"] = previous
    report["input_presence_only"] = {rel: (ROOT / rel).exists() for rel in [
        "pilot/raw", "pilot/normalized", "pilot/ledger/credit_ledger.csv",
        *[f"external/NCAALacrosseData/data/ncaa_mens_lacrosse_matchstats_{year}.csv"
          for year in range(2022, 2027)]]}
    report["candidate_files_over_50_MiB"] = [rel for rel in candidates if (ROOT/rel).is_file() and (ROOT/rel).stat().st_size > 50*1024*1024]
    report["passed"] = not any(report[k] for k in ("errors", "secret_findings", "local_path_findings", "immutable_changed"))
    if args.record:
        (PREP / "repository_check_results.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"dependencies", "immutable_absent", "imports_ok"}}, indent=2))
    print(f"Available immutable hashes checked: {report['immutable_present']}; absent: {len(report['immutable_absent'])}; imports: {len(report['imports_ok'])}.")
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
