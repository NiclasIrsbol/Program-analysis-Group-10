"""Run all configurations over the evaluation set and write CSV results."""

import argparse
import csv
import json
import statistics
from pathlib import Path

from solutions.array_bounds.domains import RELATIONAL_DOMAINS
from solutions.array_bounds.engine import ROOT, WARNING, load_method, method_from_json

import jvm

HERE = Path(__file__).resolve().parent
ARRAY_OPS = {"array_load", "array_store", "array-load", "array-store"}


def configurations():
    configs = [("interval", "interval", True)]
    for name in RELATIONAL_DOMAINS:
        if name != "interval":
            configs += [(name, name, True), (f"{name}-noexchange", name, False)]
    return configs


def discover(root=ROOT):
    """All methods in the case classes with at least one array access."""
    methods = []
    for path in sorted((root / "target" / "decompiled" / "jpamb" / "cases").glob("*.json")):
        cls = json.loads(path.read_text(encoding="utf-8"))
        cname = cls["name"].replace("/", ".")
        for m in cls["methods"]:
            code = m.get("code") or {}
            if not any(op["opr"] in ARRAY_OPS for op in code.get("bytecode", [])):
                continue
            params = jvm.ParameterType.from_json(m["params"], annotated=True)
            ret = m["returns"]["type"]
            rt = "V" if ret is None else jvm.Type.from_json(ret).encode()
            mid = f"{cname}.{m['name']}:({''.join(p.encode() for p in params)}){rt}"
            methods.append((mid, method_from_json(mid, list(params), m)))
    return methods


def read_labels(path: Path):
    labels = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                labels[(row["method"], int(row["index"]))] = row["label"].strip()
    return labels


def quartiles(xs):
    if len(xs) < 2:
        return xs[0], xs[0], xs[0]
    q = statistics.quantiles(xs, n=4, method="inclusive")
    return q[0], statistics.median(xs), q[2]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--labels", type=Path, default=HERE / "labels.csv")
    p.add_argument("--out", type=Path, default=HERE / "results")
    p.add_argument("--nonnull-params", action="store_true")
    args = p.parse_args(argv)

    from solutions.array_bounds.engine import Analysis

    methods = discover()
    labels = read_labels(args.labels)
    args.out.mkdir(parents=True, exist_ok=True)
    configs = configurations()

    access_rows, run_rows, summary_rows = [], [], []
    for cname, domain, exchange in configs:
        times, last = [], None
        for rep in range(args.runs):
            results = [
                Analysis(m, domain, exchange, nonnull_params=args.nonnull_params).run()
                for _, m in methods
            ]
            total = sum(r.runtime for r in results)
            times.append(total)
            run_rows.append({"config": cname, "run": rep, "runtime_s": f"{total:.6f}"})
            last = results

        safe = faulty = fw = det = unres = unlabeled = 0
        for r in last:
            for a in r.accesses:
                label = labels.get((r.method, a.index), "")
                access_rows.append(
                    {
                        "config": cname,
                        "method": r.method,
                        "index": a.index,
                        "offset": a.offset,
                        "kind": a.kind,
                        "label": label,
                        "verdict": a.verdict,
                        "definite": a.definite,
                        "reachable": a.reachable,
                        "null": a.null,
                        "index_itv": a.index_itv,
                        "length_itv": a.length_itv,
                        "unresolved_reason": r.unresolved or "",
                    }
                )
                unres += a.verdict == "UNRESOLVED"
                if label == "safe":
                    safe += 1
                    fw += a.verdict == WARNING
                elif label == "faulty":
                    faulty += 1
                    det += a.verdict == WARNING
                elif not label:
                    unlabeled += 1

        q1, med, q3 = quartiles(times)
        summary_rows.append(
            {
                "config": cname,
                "methods": len(last),
                "accesses": sum(len(r.accesses) for r in last),
                "safe_labelled": safe,
                "false_warnings": fw,
                "false_warning_rate": f"{fw / safe:.3f}" if safe else "",
                "faulty_labelled": faulty,
                "detected": det,
                "detection_rate": f"{det / faulty:.3f}" if faulty else "",
                "unresolved": unres,
                "unlabelled": unlabeled,
                "runtime_median_s": f"{med:.6f}",
                "runtime_iqr_s": f"{q3 - q1:.6f}",
                "steps": sum(r.steps for r in last),
                "program_points": sum(r.program_points for r in last),
                "rel_state_size": sum(r.rel_size for r in last),
            }
        )

    for name, rows in (("accesses", access_rows), ("runs", run_rows), ("summary", summary_rows)):
        with open(args.out / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)

    cols = ["config", "false_warnings", "safe_labelled", "detected", "faulty_labelled",
            "unresolved", "runtime_median_s", "runtime_iqr_s", "steps", "rel_state_size"]
    print(" | ".join(cols))
    for row in summary_rows:
        print(" | ".join(str(row[c]) for c in cols))
    print(f"\n{len(methods)} methods, results written to {args.out}")


if __name__ == "__main__":
    main()
