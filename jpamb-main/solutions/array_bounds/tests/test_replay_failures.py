import tempfile
import unittest
from pathlib import Path

from solutions.array_bounds.evaluation.replay_failures import (
    discover_array_methods,
    discover_faulty_cases,
    read_manifest,
    validate_evaluation_inventory,
)


class ReplayCaseDiscoveryTests(unittest.TestCase):
    def test_discovers_array_tagged_faulty_inputs_and_descriptors(self):
        source = """\
package example;

class ArrayCases {
  @Case("(0, [I: ]) -> out of bounds")
  @Case("(1, [I: 2]) -> ok")
  @Tag({ARRAY, LOOP})
  public static int read(int index, int[] values) {
    return 0;
  }

  @Case("() -> out of bounds")
  @Tag({LOOP})
  public static void unrelated() {}
}
"""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "ArrayCases.java").write_text(source, encoding="utf-8")
            methods = discover_array_methods(Path(directory))

        methodid = "example.ArrayCases.read:(I[I)I"
        self.assertIn(methodid, methods)
        self.assertNotIn("example.ArrayCases.unrelated:()V", methods)
        self.assertEqual(
            discover_faulty_cases(methods, supplemental_cases={}),
            [(methodid, "(0, [I: ])")],
        )

    def test_checked_in_array_inventory_is_complete_and_replayable(self):
        validate_evaluation_inventory(
            discover_array_methods(),
            read_manifest(),
        )


if __name__ == "__main__":
    unittest.main()
