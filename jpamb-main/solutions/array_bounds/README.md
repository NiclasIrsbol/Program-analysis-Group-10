# Array-bounds analyses (Group 10)

All commands run from `jpamb-main/`. Only the `jvm` package from `src/` is
needed (the engine reads `target/decompiled/` directly), so the `runit`
C-extension does not have to be installed.

| File | What | Owner |
|---|---|---|
| `syntactic.py` | tree-sitter guard detector (ISY) | done |
| `intervals.py` | interval domain, Java int32 semantics, widening | Person 1 |
| `engine.py` | shared worklist engine, transfer functions, bounds checker | Person 1 |
| `domains.py` | `RelationalDomain` interface, DBM adapter, domain registry | Person 1 |
| `difference_bounds.py` | difference-bound matrices | Person 3 |
| `guard_facts.py` | guard-fact domain (`GuardFactDomain`) | Person 2 (todo) |
| `analyse.py` | CLI for one method | Person 1 |
| `evaluate.py` | runs all configurations, 10 repetitions, writes CSVs | Person 1 |
| `labels.csv` | ground truth per access (safe / faulty / null) | to be double-checked |

## Running

```sh
python -m solutions.array_bounds.analyse "jpamb.cases.Arrays.arraySumIsLarge:([I)V" --domain interval
python -m solutions.array_bounds.analyse "jpamb.cases.Arrays.arraySumIsLarge:([I)V" --domain dbm
python -m solutions.array_bounds.analyse <methodid> --domain dbm --no-exchange --json
python -m solutions.array_bounds.evaluate --runs 10          # -> solutions/array_bounds/results/
python -m unittest solutions.array_bounds.tests.test_engine solutions.array_bounds.tests.test_intervals solutions.array_bounds.tests.test_difference_bounds
```

Each array access gets `SAFE`, `WARNING` (optionally *definite*) or
`UNRESOLVED`; null-pointer results are recorded separately (`null: safe /
possible / definite`).

## How the engine works

* State = locals + operand stack + optional relational state. Ints carry an
  interval; array references carry nullness and a length interval.
* Relational variables: `"0"` (constant zero), `L<k>` (int in local slot k),
  `#L<k>` (length of the array in local slot k).
* Stack values remember where they came from (`value == var + offset`), so
  `iload i; aload a; arraylength; if_icmpge` becomes `L_i - #L_a <= -1` on the
  fall-through edge. Constants are `"0" + k`.
* Stores/`iinc` update the relational state with `assign` and invalidate stack
  values that refer to the old version of the variable.
* Join everywhere, widening at loop heads (targets of backward jumps), so
  loops terminate without an iteration bound.
* Information exchange (reduced product) runs before every instruction:
  intervals are pushed into the relational state and tighter bounds are read
  back. `--no-exchange` disables it for the ablation.
* Sound fallbacks: an unsupported reachable opcode, exception handlers, or
  an internal error makes the method `UNRESOLVED`; int overflow goes to TOP
  and drops relations. Calls are modelled as returning an unknown value,
  which is sound because callees cannot change our locals or array lengths.

## Adding the guard-fact domain (Person 2)

Create `guard_facts.py` with a class `GuardFactDomain` implementing the
`RelationalDomain` protocol in `domains.py` (`top`, `is_bottom`, `assign`,
`forget`, `assume`, `join`, `widen`, `leq`, `upper`, `reduce`, `size`). It is
picked up automatically as `--domain guard`, and `evaluate.py` adds the `guard`
and `guard-noexchange` configurations. The minimum for useful results:

* `assume(x, y, c)` records the fact `x - y <= c` if it is one of the tracked
  predicates (e.g. `L_i - #L_a <= -1` for `i < a.length`, `"0" - L_i <= 0` for `i >= 0`).
* `upper(x, y)` returns `c` if a fact `x - y <= c` is known, else `math.inf`.
* `assign(t, s, k)` / `forget(v)` drop (or shift) facts that mention `t` / `v`.
* `join` keeps facts present in both states (intersection); `widen` can be `join`
  since the fact set is finite.
