"""Run interval and DBM configurations over the labelled array cases."""

import argparse
import csv
import json
import logging
import statistics
import sys
import time
from math import isfinite
from pathlib import Path

import jpamb
import jvm

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from interpreter import AbstractInterpreter  # noqa: E402

ARRAY_OPS = {"array_load", "array_store", "array-load", "array-store"}
CONFIGURATIONS = (
    ("interval", "interval", True),
    ("dbm", "dbm", True),
    ("dbm-noexchange", "dbm", False),
)


def _supported_parameter(parameter):
    return isinstance(parameter, (jvm.Int, jvm.Boolean)) or (
        isinstance(parameter, jvm.Array) and isinstance(parameter.contains, jvm.Int)
    )


def discover(root=ROOT):
    """Find supported static case methods that contain array accesses."""
    case_dir = root / "target" / "decompiled" / "jpamb" / "cases"
    files = sorted(case_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(
            f"No decompiled JPAMB cases found at {case_dir}. "
            f"Build the project before running the evaluation."
        )

    methods = []
    skipped = 0
    for path in files:
        cls = json.loads(path.read_text(encoding="utf-8"))
        classname = cls["name"].replace("/", ".")
        for method in cls["methods"]:
            code = method.get("code") or {}
            bytecode = code.get("bytecode", [])
            if not any(op["opr"] in ARRAY_OPS for op in bytecode):
                continue

            params = jvm.ParameterType.from_json(method["params"], annotated=True)
            if (
                "static" not in method.get("access", [])
                or code.get("exceptions")
                or not all(_supported_parameter(param) for param in params)
            ):
                skipped += 1
                continue

            return_type = method["returns"]["type"]
            return_descriptor = (
                "V" if return_type is None else jvm.Type.from_json(return_type).encode()
            )
            descriptor = "".join(param.encode() for param in params)
            methodid = (
                f"{classname}.{method['name']}:({descriptor}){return_descriptor}"
            )
            methods.append(methodid)

    if skipped:
        logging.getLogger(__name__).warning(
            "Skipped %d array-access methods unsupported by the interpreter",
            skipped,
        )
    return methods


def read_labels(path):
    labels = {}
    with open(path, newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            labels[(row["method"], int(row["index"]))] = row["label"].strip()
    return labels


def create_bytecode(root=ROOT):
    suite = jpamb.Suite.from_workdir(root)
    effect = jpamb.Effect(None)
    return suite, jpamb.Bytecode(suite, effect, {})


def analyse_method(bytecode, methodid, domain, exchange):
    started = time.perf_counter()
    analysis = AbstractInterpreter.initial(
        bytecode,
        jvm.AbsMethodID.decode(methodid),
        domain=domain,
        exchange=exchange,
    ).run()
    _, accesses = analysis.results()
    elapsed = time.perf_counter() - started
    return analysis, accesses, elapsed


def quartiles(values):
    if len(values) < 2:
        return values[0], values[0], values[0]
    q1, q3 = statistics.quantiles(values, n=4, method="inclusive")[::2]
    return q1, statistics.median(values), q3


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--labels", type=Path, default=SRC / "labels.csv")
    parser.add_argument("--out", type=Path, default=HERE / "results")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    methods = discover()
    if not methods:
        raise RuntimeError("No supported array-access methods were found")
    labels = read_labels(args.labels)
    _, bytecode = create_bytecode()
    args.out.mkdir(parents=True, exist_ok=True)

    access_rows = []
    run_rows = []
    summary_rows = []
    for config, domain, exchange in CONFIGURATIONS:
        runtimes = []
        latest = {}
        for repetition in range(1, args.runs + 1):
            total_runtime = 0.0
            for methodid in methods:
                analysis, accesses, elapsed = analyse_method(
                    bytecode, methodid, domain, exchange
                )
                total_runtime += elapsed
                latest[methodid] = (analysis, accesses)

            runtimes.append(total_runtime)
            run_rows.append(
                {
                    "config": config,
                    "run": repetition,
                    "runtime_s": f"{total_runtime:.6f}",
                }
            )

        safe = faulty = false_warnings = detected = unresolved = unlabeled = 0
        null_labelled = 0
        abstract_states = steps = rel_state_size = accesses_count = 0
        for methodid, (analysis, accesses) in latest.items():
            abstract_states += len(analysis.states)
            steps += analysis.steps
            for state in analysis.states.values():
                if state.bounds is not None and hasattr(state.bounds, "dbm"):
                    rel_state_size += sum(
                        isfinite(bound)
                        for row in state.bounds.dbm
                        for bound in row
                    )

            for offset, verdict in sorted(accesses.items()):
                label = labels.get((methodid, offset), "")
                accesses_count += 1
                access_rows.append(
                    {
                        "config": config,
                        "method": methodid,
                        "index": offset,
                        "label": label,
                        "verdict": verdict.upper(),
                        "unresolved_reason": analysis.reason or "",
                    }
                )
                unresolved += verdict == "unresolved"
                if label == "safe":
                    safe += 1
                    false_warnings += verdict == "warning"
                elif label == "faulty":
                    faulty += 1
                    detected += verdict == "warning"
                elif label == "null":
                    null_labelled += 1
                elif label not in ("null", ""):
                    unlabeled += 1

        q1, median, q3 = quartiles(runtimes)
        summary_rows.append(
            {
                "config": config,
                "methods": len(methods),
                "accesses": accesses_count,
                "safe_labelled": safe,
                "false_warnings": false_warnings,
                "false_warning_rate": f"{false_warnings / safe:.3f}" if safe else "",
                "faulty_labelled": faulty,
                "detected": detected,
                "detection_rate": f"{detected / faulty:.3f}" if faulty else "",
                "null_labelled": null_labelled,
                "unresolved": unresolved,
                "unlabelled": unlabeled,
                "runtime_median_s": f"{median:.6f}",
                "runtime_iqr_s": f"{q3 - q1:.6f}",
                "steps": steps,
                "abstract_states": abstract_states,
                "rel_state_size": rel_state_size,
            }
        )

    write_csv(
        args.out / "accesses.csv",
        access_rows,
        ["config", "method", "index", "label", "verdict", "unresolved_reason"],
    )
    write_csv(args.out / "runs.csv", run_rows, ["config", "run", "runtime_s"])
    write_csv(args.out / "summary.csv", summary_rows, list(summary_rows[0]))
    print(json.dumps(summary_rows, indent=2))
    print(f"\n{len(methods)} methods; results written to {args.out}")


if __name__ == "__main__":
    main()
