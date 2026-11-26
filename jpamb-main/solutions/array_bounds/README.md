# Array-bounds analysis

This project compares interval and difference-bound matrix (DBM) analyses for
JVM array accesses. It also includes a source-level syntactic comparison,
optional Checker Framework and JBMC comparisons, and concrete JVM replay of
known faulty cases.

Run the commands below from the `jpamb-main` repository root. The evaluation
scripts are in `solutions\array_bounds\evaluation`; labels and the evaluation
manifest are in `solutions\array_bounds\src`.

## Prerequisites and build

- Python 3.13 and `uv`
- Docker Desktop with its Linux engine running (or a configured Podman setup)

The analyses need JPAMB's compiled classes and decompiled bytecode JSON. Build
those before running an evaluation:

```powershell
uv run --active --no-sync jpamb build --compile
```

This compiles the Java cases into `target\classes` and creates their JSON
representations under `target\decompiled`. The build runs `javac` and
`jvm2json` in the JPAMB container and may take some time on the first run.

## Main DBM/interval evaluation

Run ten repetitions of each configuration (interval, DBM, and DBM without
information exchange):

```powershell
uv run --project solutions\array_bounds --no-sync python solutions\array_bounds\evaluation\evaluate.py --runs 10
```

The default output directory is
`solutions\array_bounds\evaluation\results`. It contains `runs.csv`,
`accesses.csv`, and `summary.csv`. The default ground-truth labels are read
from `solutions\array_bounds\src\labels.csv`.

## Syntactic versus DBM evaluation

Compare the source-level syntactic guard analysis against DBM:

```powershell
uv run --project solutions\array_bounds --no-sync python solutions\array_bounds\evaluation\syntactic_evaluate.py --runs 10
```

This writes `syntactic_dbm_accesses.csv` and `syntactic_dbm_summary.csv` to
the evaluation results directory.

## Checker Framework and JBMC evaluation

Run the Checker Framework Index Checker using its default location under
`tools\checker-framework`:

```powershell
uv run --project solutions\array_bounds --no-sync python solutions\array_bounds\evaluation\external_tools_evaluate.py
```

To also run JBMC with an unwind bound of 20, provide either the `jbmc`
executable or `docker`:

```powershell
uv run --project solutions\array_bounds --no-sync python solutions\array_bounds\evaluation\external_tools_evaluate.py --jbmc docker --unwind 20
```

Use `--checker-root`, `--classes`, or `--out` to override the default paths.
Results are written to
`solutions\array_bounds\evaluation\external-tools-results`.

## JVM replay evidence

Replay the concrete `@Case` inputs that expect out-of-bounds results:

```powershell
uv run --project solutions\array_bounds --no-sync python solutions\array_bounds\evaluation\replay_failures.py
```

The script checks that array-tagged case methods are represented in both
`src\labels.csv` and `src\evaluation_manifest.csv`, and that every faulty
manifest method has a concrete replay input. It writes
`jvm_replay_failures.csv` to
`solutions\array_bounds\evaluation\evaluation-results`. A `confirmed` value
of `yes` means the JVM produced an “out of bounds” result for that specific
input; it is concrete evidence, not a proof of universal safety.

## Tests

Run the full array-bounds unittest suite:

```powershell
uv run --project solutions\array_bounds --no-sync python -m unittest discover -s solutions\array_bounds\tests -t .
```

This includes interpreter behavior, interval soundness, difference-bound
operations, and evaluation/replay inventory checks. These tests use fixtures
and source files, so they do not require the JPAMB build step above.