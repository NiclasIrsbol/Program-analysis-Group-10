"""Replay known faulty ArrayBoundsProject cases on the JVM and record outcomes."""

import csv
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CLASSES = ROOT / "target" / "classes"
OUT = Path(__file__).resolve().parent / "evaluation-results"

FAILURES = [
    ("jpamb.cases.ArrayBoundsProject.inclusiveUpperBound:(I)I", ("(3)",)),
    ("jpamb.cases.ArrayBoundsProject.missingLowerBound:(I)I", ("(-1)",)),
    ("jpamb.cases.ArrayBoundsProject.missingUpperBound:(I)I", ("(3)",)),
    ("jpamb.cases.ArrayBoundsProject.staleIndexAfterArrayAssignment:()I", ("()",)),
    ("jpamb.cases.ArrayBoundsProject.unsafeDerivedIndex:(I)I", ("(3)",)),
    ("jpamb.cases.ArrayBoundsProject.staleArrayLengthRelation:(I)I", ("(2)",)),
    ("jpamb.cases.ArrayBoundsProject.staleLengthCopy:()I", ("()",)),
    ("jpamb.cases.ArrayBoundsProject.unsafeJoin:([II)I", ("([I:10, 20, 30], 1)",)),
    ("jpamb.cases.ArrayBoundsProject.unsafeComplementaryIndices:(II)I", ("(5, 0)",)),
    ("jpamb.cases.ArrayBoundsProject.unsafeProgressInvariant:()I", ("()",)),
]


def replay(methodid, inputs):
    command = [
        "java",
        "-cp",
        str(CLASSES),
        "jpamb.Runtime",
        methodid,
    ]
    command.extend(input_text for input_text in inputs if input_text)
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
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for methodid, inputs in FAILURES:
        outcome = replay(methodid, inputs)
        rows.append(
            {
                "method": methodid,
                "input": ", ".join(inputs) if any(inputs) else "()",
                "expected": "out of bounds",
                "observed": outcome,
                "confirmed": "yes" if "out of bounds" in outcome else "no",
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

    print(
        f"Replayed {len(rows)} faulty cases; "
        f"{sum(row['confirmed'] == 'yes' for row in rows)} confirmed"
    )


if __name__ == "__main__":
    main()
