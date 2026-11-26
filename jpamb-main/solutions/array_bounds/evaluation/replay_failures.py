"""Replay faulty array cases on the JVM and record their observed outcomes."""

import ast
import csv
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CASES = ROOT / "cases" / "jpamb" / "cases"
CLASSES = ROOT / "target" / "classes"
SRC = Path(__file__).resolve().parents[1] / "src"
OUT = Path(__file__).resolve().parent / "evaluation-results"

CASE_PATTERN = re.compile(r"^\s*@Case\((.*)\)\s*$")
TAG_PATTERN = re.compile(r"^\s*@Tag\(\{([^}]*)\}\)\s*$")
METHOD_PATTERN = re.compile(
    r"^\s*public\s+static\s+([\w\[\]]+)\s+([A-Za-z_$][\w$]*)\s*"
    r"\(([^)]*)\)"
)
JAVA_TYPES = {
    "boolean": "Z",
    "char": "C",
    "int": "I",
    "void": "V",
}

# This helper has no @Case annotation, but its zero-length input is the source
# of the faulty case in Calls.allPrimesArePositive.
SUPPLEMENTAL_CASES = {
    "jpamb.cases.Calls.generatePrimeArray:(I)[I": ("(0)",),
}


def type_descriptor(java_type):
    dimensions = 0
    while java_type.endswith("[]"):
        java_type = java_type[:-2].strip()
        dimensions += 1
    if java_type not in JAVA_TYPES or java_type == "void" and dimensions:
        raise ValueError(f"Unsupported Java type in array cases: {java_type}")
    return "[" * dimensions + JAVA_TYPES[java_type]


def method_descriptor(return_type, parameters):
    parameter_descriptors = []
    for parameter in parameters.split(","):
        parameter = parameter.strip()
        if parameter:
            java_type = parameter.rsplit(None, 1)[0]
            parameter_descriptors.append(type_descriptor(java_type))
    return (
        "("
        + "".join(parameter_descriptors)
        + ")"
        + type_descriptor(return_type)
    )


def discover_array_methods(cases_dir=CASES):
    """Return methods tagged ARRAY and their declared concrete test cases."""
    methods = {}
    for source in sorted(cases_dir.glob("*.java")):
        text = source.read_text(encoding="utf-8")
        package_match = re.search(r"^\s*package\s+([\w.]+)\s*;", text, re.MULTILINE)
        if not package_match:
            raise ValueError(f"Missing package declaration in {source}")
        package = package_match.group(1)
        class_name = source.stem
        pending_cases = []
        pending_tags = set()

        for line in text.splitlines():
            case_match = CASE_PATTERN.match(line)
            if case_match:
                pending_cases.append(ast.literal_eval(case_match.group(1).strip()))
                continue

            tag_match = TAG_PATTERN.match(line)
            if tag_match:
                pending_tags.update(
                    tag.strip() for tag in tag_match.group(1).split(",")
                )
                continue

            method_match = METHOD_PATTERN.match(line)
            if not method_match:
                continue

            if "ARRAY" in pending_tags:
                return_type, name, parameters = method_match.groups()
                methodid = (
                    f"{package}.{class_name}.{name}"
                    f":{method_descriptor(return_type, parameters)}"
                )
                methods[methodid] = tuple(pending_cases)
            pending_cases = []
            pending_tags = set()

    return methods


def discover_faulty_cases(array_methods, supplemental_cases=SUPPLEMENTAL_CASES):
    """Collect every ARRAY-tagged @Case expecting an out-of-bounds result."""
    failures = []
    for methodid, cases in array_methods.items():
        for case in cases:
            inputs, separator, outcome = case.rpartition("->")
            if separator and outcome.strip().lower() == "out of bounds":
                failures.append((methodid, inputs.strip()))

    for methodid, inputs in supplemental_cases.items():
        failures.extend((methodid, input_text) for input_text in inputs)

    return list(dict.fromkeys(failures))


def read_manifest(path=SRC / "evaluation_manifest.csv"):
    with open(path, newline="", encoding="utf-8") as file:
        return {
            row["method"]: row["expected_outcome"].strip()
            for row in csv.DictReader(file)
        }


def validate_evaluation_inventory(array_methods, manifest, labels_path=SRC / "labels.csv"):
    missing_manifest = set(array_methods) - set(manifest)
    if missing_manifest:
        raise RuntimeError(
            "Array-tagged methods missing from evaluation_manifest.csv: "
            + ", ".join(sorted(missing_manifest))
        )

    with open(labels_path, newline="", encoding="utf-8") as file:
        labelled_methods = {row["method"] for row in csv.DictReader(file)}
    missing_labels = set(array_methods) - labelled_methods
    if missing_labels:
        raise RuntimeError(
            "Array-tagged methods missing from labels.csv: "
            + ", ".join(sorted(missing_labels))
        )

    failures = discover_faulty_cases(array_methods)
    replay_methods = {methodid for methodid, _ in failures}
    missing_replays = {
        methodid
        for methodid, outcome in manifest.items()
        if outcome == "faulty" and methodid not in replay_methods
    }
    if missing_replays:
        raise RuntimeError(
            "Faulty manifest methods lack a concrete JVM replay input: "
            + ", ".join(sorted(missing_replays))
        )
    return failures


def replay(methodid, inputs):
    command = [
        "java",
        "-cp",
        str(CLASSES),
        "jpamb.Runtime",
        methodid,
        inputs,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    stdout = result.stdout.strip()
    if stdout:
        return stdout
    stderr = result.stderr.strip()
    if stderr:
        return stderr
    return f"process exited with code {result.returncode}" if result.returncode else "no outcome"


def main():
    if not CLASSES.is_dir():
        raise FileNotFoundError(
            f"Compiled JPAMB classes not found at {CLASSES}. "
            "Build the project before replaying JVM cases."
        )

    array_methods = discover_array_methods()
    manifest = read_manifest()
    failures = validate_evaluation_inventory(array_methods, manifest)

    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for methodid, inputs in failures:
        outcome = replay(methodid, inputs)
        rows.append(
            {
                "method": methodid,
                "input": inputs,
                "expected": "out of bounds",
                "observed": outcome,
                "confirmed": "yes" if "out of bounds" in outcome.lower() else "no",
            }
        )

    with open(
        OUT / "jvm_replay_failures.csv",
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["method", "input", "expected", "observed", "confirmed"],
        )
        writer.writeheader()
        writer.writerows(rows)

    confirmed = sum(row["confirmed"] == "yes" for row in rows)
    method_count = len({row["method"] for row in rows})
    print(
        f"Replayed {len(rows)} faulty cases across {method_count} methods; "
        f"{confirmed} confirmed"
    )


if __name__ == "__main__":
    main()
