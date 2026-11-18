"""Run only the reviewed collaboration checks; never run a research entry point."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / "repo_prep/2026-11-18/test_results.json"
GROUPS = {
    "pilot": [
        "tests/test_odds_client.py", "tests/test_stage_a.py", "tests/test_stage_b.py",
        "tests/test_stage_c.py", "tests/test_stage_d.py", "tests/test_normalize.py",
        "tests/test_analysis.py", "tests/test_costs.py",
        "-k", "not test_parity_on_saved_anchor_events",
    ],
    "join": [
        "--noconftest", "tests/test_join_audit.py", "tests/test_join_revision.py",
        "-k", "not test_13_golden_four_known_events and not test_14_coverage_reproduction "
        "and not test_11_real_four_known_events_run2_path "
        "and not test_12_real_flip_invariance_on_run1_primary",
    ],
    "engine": [
        "--noconftest", "elo_feas/tests/test_elo_engine.py", "-k",
        "not test_e6_frozen_params_gate and not test_e6_selection_loader_reads_only_2022_2024 "
        "and not test_e6_2025_predictions_are_pre_game",
    ],
    "adapters": [
        "--noconftest", "elo_feas/tests/test_elo_data_layer.py", "-k",
        "test_t3_longest_prefix or test_t3_word_boundary_and_failures "
        "or test_t4_adapter_fails_loudly or test_t5_outcome_column_never_carried "
        "or test_t6_synthetic_flip_of_each_pair",
    ],
}


def install_guard() -> None:
    """Deny network, private input reads and workspace writes in a test child."""
    def guard(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.gethostbyname"}:
            raise RuntimeError("network blocked by collaboration checks")
        if event != "open" or isinstance(args[0], int):
            return
        path = Path(os.fsdecode(args[0])).resolve()
        try:
            rel = path.relative_to(ROOT).as_posix().lower()
        except ValueError:
            return
        mode, flags = args[1:3]
        write = any(c in (mode or "") for c in "wax+") or bool(
            (flags or 0) & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        )
        if write:
            raise RuntimeError("workspace writes blocked by collaboration checks")
        if path.name.lower() == ".env" or (
            path.name.lower().startswith(".env.") and path.name.lower() != ".env.example"
        ):
            raise RuntimeError("credential file reads blocked")
        if rel.startswith(("pilot/raw/", "pilot/normalized/", "pilot/ledger/")) or rel == "pilot/cradl_observations.csv":
            raise RuntimeError("private input reads blocked")
        if rel.startswith("external/") and path.name.lower().endswith(("_2025.csv", "_2026.csv")):
            raise RuntimeError("raw 2025/2026 results reads blocked")
    sys.addaudithook(guard)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=GROUPS, help=argparse.SUPPRESS)
    parser.add_argument("--record", action="store_true", help="save preparation test evidence")
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.dont_write_bytecode = True
    if args.group:
        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        sys.path.insert(0, str(ROOT))
        install_guard()
        import pytest
        return int(pytest.main(["-q", *GROUPS[args.group]]))

    env = os.environ.copy()
    env.pop("ODDS_API_KEY", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    records = []
    for name in GROUPS:
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "--group", name]
        start = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
        print(f"[{name}]", flush=True)
        print(result.stdout, end="", flush=True)
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr, flush=True)
        records.append({"group": name, "pytest_args": ["-q", *GROUPS[name]],
                        "returncode": result.returncode, "seconds": round(time.monotonic()-start, 3),
                        "stdout": result.stdout, "stderr": result.stderr})
    if args.record:
        RECORD.write_text(json.dumps(records, indent=2)+"\n", encoding="utf-8")
    return int(any(r["returncode"] != 0 for r in records))


if __name__ == "__main__":
    raise SystemExit(main())
