"""Compare the source-level syntactic analyser with the DBM analyser."""

import argparse
import csv
import importlib
import json
import logging
import sys
import time
from pathlib import Path

import jvm
from solutions.array_bounds.engine import Analysis
from solutions.array_bounds.evaluate import ROOT, discover, read_labels

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

syntactic_module = importlib.import_module("syntactic")
analyse_source = syntactic_module.analyse_source
SourceMethodNotFoundError = syntactic_module.SourceMethodNotFoundError


def source_file(methodid):
    classname = jvm.AbsMethodID.decode(methodid).classname
    return ROOT / "cases" / Path(*classname.packages) / f"{classname.name}.java"


def syntactic_verdicts(methodid) -> list[str] | None:
    decoded = jvm.AbsMethodID.decode(methodid)
    try:
        results = analyse_source(source_file(methodid), decoded)
    except SourceMethodNotFoundError:
        logging.getLogger(__name__).warning(
            "%s has no matching source declaration; recording its accesses as UNKNOWN",
            methodid,
        )
        return None
    return ["SAFE" if result == "guarded" else "UNKNOWN" for result in results]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--labels", type=Path, default=Path(__file__).with_name("labels.csv"))
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("results"))
    args = parser.parse_args(argv)

    methods = discover()
    labels = read_labels(args.labels)
    args.out.mkdir(parents=True, exist_ok=True)

    rows = []
    runtimes = {"syntactic": [], "dbm": []}
    last_dbm = {}
    last_syntactic = {}

    for _ in range(args.runs):
        started = time.perf_counter()
        syntactic = {}
        for methodid, _method in methods:
            syntactic[methodid] = syntactic_verdicts(methodid)
        runtimes["syntactic"].append(time.perf_counter() - started)

        started = time.perf_counter()
        dbm = {}
        for methodid, method in methods:
            result = Analysis(method, "dbm", exchange=True).run()
            dbm[methodid] = result
        runtimes["dbm"].append(time.perf_counter() - started)

        last_syntactic = syntactic
        last_dbm = dbm

    for methodid, _method in methods:
        result = last_dbm[methodid]
        dbm_accesses = [
            access
            for access in sorted(result.accesses, key=lambda access: access.index)
            if (methodid, access.index) in labels
        ]
        syntactic = last_syntactic[methodid]
        if not dbm_accesses:
            continue
        if syntactic is None:
            syntactic = ["UNKNOWN"] * len(dbm_accesses)
        else:
            if len(syntactic) > len(dbm_accesses):
                raise RuntimeError(
                    f"{methodid}: syntactic found {len(syntactic)} accesses, "
                    f"but labels contain {len(dbm_accesses)} labelled accesses"
                )
            # The bytecode analyser reports array-initialiser stores, while the
            # source analyser reports only Java array-access expressions.
            syntactic = ["SAFE"] * (len(dbm_accesses) - len(syntactic)) + syntactic

        for ordinal, (access, syntactic_verdict) in enumerate(
            zip(dbm_accesses, syntactic)
        ):
            label = labels.get((methodid, access.index), "")
            rows.append(
                {
                    "method": methodid,
                    "index": access.index,
                    "ordinal": ordinal,
                    "label": label,
                    "syntactic_verdict": syntactic_verdict,
                    "dbm_verdict": access.verdict,
                }
            )

    with open(args.out / "syntactic_dbm_accesses.csv", "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

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
                "accesses": len(rows),
                "safe_labelled": len(safe),
                "false_warnings": false_warnings,
                "false_warning_rate": f"{false_warnings / len(safe):.3f}" if safe else "",
                "faulty_labelled": len(faulty),
                "detected": detected,
                "detection_rate": f"{detected / len(faulty):.3f}" if faulty else "",
                "unknown_or_unresolved": sum(
                    row[verdict_field] in ("UNKNOWN", "UNRESOLVED") for row in rows
                ),
                "runtime_median_s": f"{sorted(runtime)[len(runtime) // 2]:.6f}",
            }
        )

    with open(args.out / "syntactic_dbm_summary.csv", "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
