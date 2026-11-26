"""Compare the source-level syntactic analyser with DBM."""

import argparse
import json
import logging
import statistics
import time
from pathlib import Path

import jvm

if __package__:
    from .evaluate import (
        HERE,
        ROOT,
        analyse_method,
        create_bytecode,
        discover,
        read_labels,
        write_csv,
    )
else:
    from evaluate import (
        HERE,
        ROOT,
        analyse_method,
        create_bytecode,
        discover,
        read_labels,
        write_csv,
    )

from syntactic import SourceMethodNotFoundError, analyse_source


def source_file(methodid):
    classname = jvm.AbsMethodID.decode(methodid).classname
    return ROOT / "cases" / Path(*classname.packages) / f"{classname.name}.java"


def syntactic_verdicts(methodid):
    decoded = jvm.AbsMethodID.decode(methodid)
    try:
        results = analyse_source(source_file(methodid), decoded)
    except SourceMethodNotFoundError:
        logging.getLogger(__name__).warning(
            "%s has no matching source declaration; recording accesses as UNKNOWN",
            methodid,
        )
        return None
    return ["SAFE" if result == "guarded" else "UNKNOWN" for result in results]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--labels", type=Path, default=HERE.parent / "src" / "labels.csv")
    parser.add_argument("--out", type=Path, default=HERE / "results")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    methods = discover()
    labels = read_labels(args.labels)
    _, bytecode = create_bytecode()
    args.out.mkdir(parents=True, exist_ok=True)

    runtimes = {"syntactic": [], "dbm": []}
    last_syntactic = {}
    last_dbm = {}
    for _ in range(args.runs):
        started = time.perf_counter()
        syntactic = {methodid: syntactic_verdicts(methodid) for methodid in methods}
        runtimes["syntactic"].append(time.perf_counter() - started)

        started = time.perf_counter()
        dbm = {}
        for methodid in methods:
            analysis, accesses, _ = analyse_method(bytecode, methodid, "dbm", True)
            dbm[methodid] = (analysis, accesses)
        runtimes["dbm"].append(time.perf_counter() - started)
        last_syntactic = syntactic
        last_dbm = dbm

    rows = []
    for methodid in methods:
        analysis, accesses = last_dbm[methodid]
        dbm_accesses = [
            (offset, verdict)
            for offset, verdict in sorted(accesses.items())
            if (methodid, offset) in labels
        ]
        if not dbm_accesses:
            continue

        syntactic = last_syntactic[methodid]
        if syntactic is None:
            syntactic = ["UNKNOWN"] * len(dbm_accesses)
        else:
            if len(syntactic) > len(dbm_accesses):
                raise RuntimeError(
                    f"{methodid}: syntactic found {len(syntactic)} accesses, "
                    f"but only {len(dbm_accesses)} accesses are labelled"
                )
            # Array-initialiser stores have no corresponding source access.
            syntactic = ["SAFE"] * (len(dbm_accesses) - len(syntactic)) + syntactic

        for ordinal, ((offset, verdict), syntactic_verdict) in enumerate(
            zip(dbm_accesses, syntactic)
        ):
            rows.append(
                {
                    "method": methodid,
                    "index": offset,
                    "ordinal": ordinal,
                    "label": labels[(methodid, offset)],
                    "syntactic_verdict": syntactic_verdict,
                    "dbm_verdict": verdict.upper(),
                    "unresolved_reason": analysis.reason or "",
                }
            )

    access_fields = [
        "method",
        "index",
        "ordinal",
        "label",
        "syntactic_verdict",
        "dbm_verdict",
        "unresolved_reason",
    ]
    write_csv(args.out / "syntactic_dbm_accesses.csv", rows, access_fields)

    summary = []
    for config in ("syntactic", "dbm"):
        verdict_field = f"{config}_verdict"
        safe = [row for row in rows if row["label"] == "safe"]
        faulty = [row for row in rows if row["label"] == "faulty"]
        false_warnings = sum(row[verdict_field] != "SAFE" for row in safe)
        detected = sum(row[verdict_field] != "SAFE" for row in faulty)
        runtime = runtimes[config]
        summary.append(
            {
                "config": config,
                "methods": len(methods),
                "accesses": len(rows),
                "safe_labelled": len(safe),
                "false_warnings": false_warnings,
                "false_warning_rate": (
                    f"{false_warnings / len(safe):.3f}" if safe else ""
                ),
                "faulty_labelled": len(faulty),
                "detected": detected,
                "detection_rate": f"{detected / len(faulty):.3f}" if faulty else "",
                "unknown_or_unresolved": sum(
                    row[verdict_field] in ("UNKNOWN", "UNRESOLVED") for row in rows
                ),
                "runtime_median_s": f"{statistics.median(runtime):.6f}",
            }
        )

    write_csv(args.out / "syntactic_dbm_summary.csv", summary, list(summary[0]))
    print(json.dumps(summary, indent=2))
    print(f"\n{len(methods)} methods; results written to {args.out}")


if __name__ == "__main__":
    main()
