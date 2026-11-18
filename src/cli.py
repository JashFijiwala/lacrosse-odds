"""Pilot command line. Run with: .\\.venv\\Scripts\\python.exe -m src.cli <command>"""
from __future__ import annotations

import argparse
import sys

from . import halt as H
from .odds_client import OddsClient, OddsClientError


def cmd_plan(client: OddsClient, args) -> int:
    print(f"credit cap: {client.cap}")
    for stage, budget in client.budgets.items():
        print(f"  {stage:<8} {budget:>4}")
    print(f"  {'total':<8} {sum(client.budgets.values()):>4}")
    return 0


def cmd_ledger_status(client: OddsClient, args) -> int:
    s = client.ledger.summary(client.budgets)
    print(f"{'stage':<10}{'spent':>7}{'budget':>8}{'left':>7}")
    for stage, budget in client.budgets.items():
        if stage == "RESERVE":
            spent = s["reserve_used"]
        else:
            spent = s["per_stage"].get(stage, 0) - s["reserve_by_stage"].get(stage, 0)
        print(f"{stage:<10}{spent:>7}{budget:>8}{budget - spent:>7}")
    other = {k: v for k, v in s["per_stage"].items() if k not in client.budgets}
    for stage, spent in other.items():
        print(f"{stage:<10}{spent:>7}{'-':>8}{'-':>7}")
    print(f"header-reported spend since baseline: {s['header_spent']}")
    print(f"pessimistic committed: {s['committed']} / cap {client.cap} (remaining {client.cap - s['committed']})")
    if s["unresolved"]:
        print("UNRESOLVED uncertain requests (counted at expected max):")
        for r in s["unresolved"]:
            print(f"  {r['fingerprint']}  stage={r['stage']} status={r['status_code']} "
                  f"actual={r['actual_cost']} expected_max={r['expected_cost_max']} endpoint={r['endpoint']}")
    else:
        print("no unresolved uncertain requests")
    halt = H.read_halt(client.halt_path)
    if halt:
        print(f"HALT ACTIVE: {halt.get('reason_code')} fp={halt.get('fingerprint')} - {halt.get('reason_text')}")
    if client.pending_path.exists():
        print("pending.json present")
    return 0


def cmd_bootstrap(client: OddsClient, args) -> int:
    base = client.bootstrap_baseline()
    print(f"baseline written: used={base['used']} remaining={base['remaining']}")
    return 0


def cmd_reconcile(client: OddsClient, args) -> int:
    out = client.reconcile(args.fingerprint)
    print(f"RECONCILED {out['fingerprint']}: unexplained usage {out['unexplained_diff']}"
          + (" (response unrecoverable; refetch_of recorded)" if out["unrecoverable"] else ""))
    return 0


def cmd_clear_halt(client: OddsClient, args) -> int:
    client.clear_halt(args.reason_code, args.resolution)
    print(f"HALT {args.reason_code} cleared and recorded in halt_history.jsonl")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cli")
    p.add_argument("--root", default=".", help="project root (default: current directory)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("plan").set_defaults(func=cmd_plan)
    sub.add_parser("ledger-status").set_defaults(func=cmd_ledger_status)
    sub.add_parser("bootstrap-baseline").set_defaults(func=cmd_bootstrap)
    r = sub.add_parser("reconcile")
    r.add_argument("--fingerprint", required=True)
    r.set_defaults(func=cmd_reconcile)
    c = sub.add_parser("clear-halt")
    c.add_argument("--reason-code", required=True, choices=sorted(H.VALID_CODES))
    c.add_argument("--resolution", required=True)
    c.set_defaults(func=cmd_clear_halt)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = OddsClient(root=args.root)
        return args.func(client, args)
    except OddsClientError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
