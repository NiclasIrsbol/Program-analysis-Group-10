"""CLI: python -m solutions.array_bounds.analyse <methodid> [--domain dbm] [--json]"""

import argparse
import json
import platform
import sys

from solutions.array_bounds.domains import RELATIONAL_DOMAINS
from solutions.array_bounds.engine import analyse

QUERIES = ("*", "assertion error", "divide by zero", "null pointer", "ok", "out of bounds")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["info"]:
        print("array-bounds", "1.0", "Group 10", "static,python,relational", platform.platform(), sep="\n")
        return 0

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("methodid")
    p.add_argument("--domain", default="dbm", choices=sorted(RELATIONAL_DOMAINS))
    p.add_argument("--no-exchange", action="store_true", help="disable interval<->relational exchange")
    p.add_argument("--nonnull-params", action="store_true", help="assume array parameters are non-null")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = p.parse_args(argv)

    r = analyse(
        args.methodid,
        args.domain,
        exchange=not args.no_exchange,
        nonnull_params=args.nonnull_params,
    )

    if args.json:
        print(json.dumps(r.to_dict(), indent=2))
        return 0

    err = sys.stderr
    print(f"{r.method}  [{r.domain}{'' if r.exchange else ', no exchange'}]  -> {r.summary}", file=err)
    if r.unresolved:
        print(f"  unresolved: {r.unresolved}", file=err)
    for a in r.accesses:
        tag = " (definite)" if a.definite else ""
        reach = "" if a.reachable else " (unreachable)"
        print(
            f"  @{a.offset:<4} {a.kind:5} {a.verdict}{tag}{reach}  "
            f"index {a.index_itv} length {a.length_itv}  null:{a.null}",
            file=err,
        )
    print(f"  steps={r.steps} points={r.program_points} time={r.runtime * 1000:.1f}ms", file=err)

    for q in QUERIES:
        possible = r.unresolved is not None or q in r.outcomes
        print(f"{q};{'maybe' if possible else 'no'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
