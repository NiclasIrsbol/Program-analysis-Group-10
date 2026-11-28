"""Run and normalize existing-tool comparisons for array-bounds methods."""

import argparse
import csv
import re
import shutil
import statistics
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
CLASSES = ROOT / "target" / "classes"
METHOD_RE = re.compile(r"public static [^\n{]+ ([A-Za-z_][A-Za-z0-9]*)\s*\(")


def read_labels(path):
    labels = {}
    with open(path, newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            labels.setdefault(row["method"], set()).add(row["label"].strip())
    return labels


def method_at_line(source, line):
    current = None
    brace_depth = 0
    for number, text in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        match = METHOD_RE.search(text)
        if match:
            current = match.group(1)
        brace_depth += text.count("{") - text.count("}")
        if number == line:
            return current
    return current


def labelled_methods(labels):
    methods = {}
    for methodid, types in labels.items():
        if "null" in types:
            label = "null"
        elif "faulty" in types:
            label = "faulty"
        else:
            label = "safe"
        methods[methodid] = label
    return methods


def checker_framework(checker_root, sources, out):
    checker_jar = checker_root / "checker" / "dist" / "checker.jar"
    if not checker_jar.exists():
        raise FileNotFoundError(checker_jar)

    classes = out / "checker-classes"
    classes.mkdir(parents=True, exist_ok=True)
    command = [
        "java",
        "-jar",
        str(checker_jar),
        "-processor",
        "org.checkerframework.checker.index.IndexChecker",
        "-classpath",
        str(ROOT / "target" / "classes"),
        "-d",
        str(classes),
        *[str(source) for source in sources],
    ]
    started = time.perf_counter()
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    runtime = time.perf_counter() - started

    warnings = []
    for line in (process.stdout + "\n" + process.stderr).splitlines():
        match = re.match(r"(.+\.java):(\d+): error: \[(.+)\] (.+)", line)
        if not match:
            continue
        source_text, line_number, category, message = match.groups()
        source = Path(source_text)
        if not source.is_absolute():
            source = ROOT / source
        method_name = method_at_line(source, int(line_number))
        if method_name:
            warnings.append(
                {
                    "source": source.as_posix(),
                    "line": int(line_number),
                    "category": category,
                    "method": method_name,
                    "message": message,
                }
            )

    raw = out / "checker_framework_raw.txt"
    raw.write_text(process.stdout + process.stderr, encoding="utf-8")
    return warnings, runtime, process.returncode


def jbmc(jbmc_executable, methods, out, classes, unwind=20):
    if not jbmc_executable:
        return [], None

    if not classes.is_dir():
        raise FileNotFoundError(
            f"Compiled JPAMB classes not found at {classes}. "
            "Build the project before running JBMC."
        )

    docker_mode = jbmc_executable == "docker"
    if docker_mode:
        executable = "docker"
    else:
        executable = shutil.which(jbmc_executable) or jbmc_executable
        if not Path(executable).exists() and shutil.which(executable) is None:
            raise FileNotFoundError(executable)

    rows = []
    for methodid in methods:
        if docker_mode:
            classes_path = str(classes)
            inner = (
                "cd /mnt/classes && jbmc "
                f"'{methodid}' --cp /mnt/classes --unwind {unwind}"
            )
            command = [
                "docker",
                "run",
                "--rm",
                "--mount",
                f"type=bind,source={classes_path},target=/mnt/classes,readonly",
                "ghcr.io/diffblue/cbmc:6.11.0",
                "sh",
                "-lc",
                inner,
            ]
        else:
            command = [
                executable,
                methodid,
                "--cp",
                str(classes),
                "--unwind",
                str(unwind),
            ]
        started = time.perf_counter()
        process = subprocess.run(command, capture_output=True, text=True, check=False)
        runtime = time.perf_counter() - started
        combined = process.stdout + "\n" + process.stderr
        bound_lines = [
            line.strip()
            for line in combined.splitlines()
            if "array-index-out-of-bounds-" in line
            and methodid in line
        ]
        failures = [line for line in bound_lines if ": FAILURE" in line]
        successes = [line for line in bound_lines if ": SUCCESS" in line]
        if failures:
            result = "FAULT_FOUND"
        elif successes:
            result = "SAFE"
        else:
            result = "UNKNOWN"
        rows.append(
            {
                "tool": "jbmc",
                "method": methodid,
                "label": methods.get(methodid, ""),
                "result": result,
                "bound_properties": len(bound_lines),
                "successes": len(successes),
                "failures": len(failures),
                "unwind": unwind,
                "runtime_s": f"{runtime:.6f}",
                "returncode": process.returncode,
            }
        )
    (out / "jbmc_raw.txt").write_text(
        "\n".join(
            f"{row['method']}|{row['result']}|{row['returncode']}|"
            f"{row['successes']} success|{row['failures']} failure"
            for row in rows
        ),
        encoding="utf-8",
    )
    return rows, (
        sum(float(row["runtime_s"]) for row in rows) / len(rows)
        if rows
        else None
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checker-root",
        type=Path,
        default=(
            ROOT
            / "tools"
            / "checker-framework"
            / "checker-framework-4.2.0"
        ),
    )
    parser.add_argument(
        "--sources",
        nargs="*",
        type=Path,
        default=[
            ROOT / "cases" / "jpamb" / "cases" / "Arrays.java",
            ROOT / "cases" / "jpamb" / "cases" / "Calls.java",
            ROOT / "cases" / "jpamb" / "cases" / "ArrayBoundsProject.java",
        ],
    )
    parser.add_argument("--labels", type=Path, default=SRC / "labels.csv")
    parser.add_argument("--out", type=Path, default=HERE / "external-tools-results")
    parser.add_argument("--jbmc", default=None)
    parser.add_argument("--classes", type=Path, default=CLASSES)
    parser.add_argument("--unwind", type=int, default=20)
    args = parser.parse_args(argv)
    args.classes = args.classes.resolve()
    args.out.mkdir(parents=True, exist_ok=True)

    if not args.classes.is_dir():
        raise FileNotFoundError(
            f"Compiled JPAMB classes not found at {args.classes}. "
            "Build the project before comparing external tools."
        )

    labels = read_labels(args.labels)
    methods = labelled_methods(labels)
    checker_warnings, checker_runtime, checker_returncode = checker_framework(
        args.checker_root, args.sources, args.out
    )

    warned_methods = set()
    rows = []
    for warning in checker_warnings:
        for methodid, label in methods.items():
            if f".{warning['method']}:" in methodid:
                warned_methods.add(methodid)
                rows.append(
                    {
                        "tool": "checker-framework",
                        "method": methodid,
                        "label": label,
                        "result": "WARNING",
                        "category": warning["category"],
                        "source_line": warning["line"],
                        "runtime_s": f"{checker_runtime:.6f}",
                        "notes": warning["message"],
                    }
                )

    normalized_rows = []
    for methodid, label in methods.items():
        if label == "null":
            continue
        normalized_rows.append(
            {
                "tool": "checker-framework",
                "method": methodid,
                "label": label,
                "result": "WARNING" if methodid in warned_methods else "SAFE",
                "category": "",
                "source_line": "",
                "runtime_s": f"{checker_runtime:.6f}",
                "notes": "",
            }
        )
    rows = normalized_rows

    with open(args.out / "checker_framework_accesses.csv", "w", newline="", encoding="utf-8") as file:
        fieldnames = [
            "tool",
            "method",
            "label",
            "result",
            "category",
            "source_line",
            "runtime_s",
            "notes",
        ]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    returncode_row = args.out / "checker_framework_environment.txt"
    returncode_row.write_text(
        f"returncode={checker_returncode}\n",
        encoding="utf-8",
    )
    safe = [row for row in normalized_rows if row["label"] == "safe"]
    faulty = [row for row in normalized_rows if row["label"] == "faulty"]
    false_warnings = sum(
        row["label"] == "safe" and row["result"] == "WARNING"
        for row in normalized_rows
    )
    detected = sum(
        row["label"] == "faulty" and row["result"] == "WARNING"
        for row in normalized_rows
    )
    summary = [
        {
            "tool": "checker-framework",
            "methods": len(normalized_rows),
            "safe_labelled": len(safe),
            "false_warnings": false_warnings,
            "false_warning_rate": f"{false_warnings / len(safe):.3f}" if safe else "",
            "faulty_labelled": len(faulty),
            "detected": detected,
            "detection_rate": f"{detected / len(faulty):.3f}" if faulty else "",
            "unknown": 0,
            "runtime_s": f"{checker_runtime:.6f}",
        }
    ]
    with open(args.out / "external_tools_summary.csv", "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    print(
        f"Checker Framework: {len(checker_warnings)} diagnostics in "
        f"{checker_runtime:.3f}s"
    )

    jbmc_rows, _ = jbmc(
        args.jbmc, methods, args.out, args.classes, args.unwind
    )
    if jbmc_rows:
        with open(args.out / "jbmc_results.csv", "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=list(jbmc_rows[0]))
            writer.writeheader()
            writer.writerows(jbmc_rows)

        resolved = [row for row in jbmc_rows if row["result"] != "UNKNOWN"]
        safe = [row for row in jbmc_rows if row["label"] == "safe"]
        faulty = [row for row in jbmc_rows if row["label"] == "faulty"]
        false_warnings = sum(
            row["label"] == "safe" and row["result"] == "FAULT_FOUND"
            for row in jbmc_rows
        )
        detected = sum(
            row["label"] == "faulty" and row["result"] == "FAULT_FOUND"
            for row in jbmc_rows
        )
        metric_rows = [row for row in jbmc_rows if row["label"] in ("safe", "faulty")]
        runtime_s = sum(
            float(row["runtime_s"]) for row in metric_rows
        )
        jbmc_summary = {
            "tool": "jbmc",
            "methods": len(safe) + len(faulty),
            "safe_labelled": len(safe),
            "false_warnings": false_warnings,
            "false_warning_rate": f"{false_warnings / len(safe):.3f}" if safe else "",
            "faulty_labelled": len(faulty),
            "detected": detected,
            "detection_rate": f"{detected / len(faulty):.3f}" if faulty else "",
            "unknown": len(jbmc_rows) - len(resolved),
            "runtime_s": f"{runtime_s:.6f}",
        }
        append_summary = args.out / "external_tools_summary.csv"
        with open(append_summary, newline="", encoding="utf-8") as file:
            existing = list(csv.DictReader(file))
        existing.append(jbmc_summary)
        fieldnames = list(existing[0])
        with open(append_summary, "w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(existing)


if __name__ == "__main__":
    main()
