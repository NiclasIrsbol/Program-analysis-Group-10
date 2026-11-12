package jpamb.cases;

import static jpamb.utils.Tag.TagType.*;

import jpamb.utils.*;

public class ArrayBoundsProject {

  // Pair 1: Complete bounds check vs missing upper bound

  @Case("(1) -> ok")
  @Tag({ARRAY})
  public static int guardedAccess(int i) {
    int[] array = {10, 20, 30};

    if (i >= 0 && i < array.length) {
      return array[i];
    }

    return 0;
  }

  @Case("(1) -> ok")
  @Case("(3) -> out of bounds")
  @Tag({ARRAY})
  public static int missingUpperBound(int i) {
    int[] array = {10, 20, 30};

    if (i >= 0) {
      return array[i];
    }

    return 0;
  }

  // Pair 2: Strict upper bound vs inclusive upper bound

  @Case("(2) -> ok")
  @Tag({ARRAY})
  public static int strictUpperBound(int i) {
    int[] array = {10, 20, 30};

    if (i >= 0 && i < array.length) {
      return array[i];
    }

    return 0;
  }

  @Case("(2) -> ok")
  @Case("(3) -> out of bounds")
  @Tag({ARRAY})
  public static int inclusiveUpperBound(int i) {
    int[] array = {10, 20, 30};

    if (i >= 0 && i <= array.length) {
      return array[i];
    }

    return 0;
  }

  // Pair 3: Recheck after array reassignment vs stale index

  @Case("() -> ok")
  @Tag({ARRAY})
  public static int recheckAfterArrayAssignment() {
    int[] array = new int[5];
    int i = array.length - 1;

    array = new int[2];

    if (i >= 0 && i < array.length) {
      return array[i];
    }

    return 0;
  }

  @Case("() -> out of bounds")
  @Tag({ARRAY})
  public static int staleIndexAfterArrayAssignment() {
    int[] array = new int[5];
    int i = array.length - 1;

    array = new int[2];

    if (i >= 0) {
      return array[i];
    }

    return 0;
  }

  // Pair 4: Safe derived index vs unsafe derived index

  @Case("(1) -> ok")
  @Case("(2) -> ok")
  @Tag({ARRAY})
  public static int safeDerivedIndex(int i) {
    int[] array = {10, 20, 30, 40};

    int j = i + 1;

    if (i >= 0 && i < array.length - 1) {
      return array[j];
    }

    return 0;
  }

  @Case("(1) -> ok")
  @Case("(3) -> out of bounds")
  @Tag({ARRAY})
  public static int unsafeDerivedIndex(int i) {
    int[] array = {10, 20, 30, 40};

    int j = i + 1;

    if (i >= 0 && i < array.length) {
      return array[j];
    }

    return 0;
  }
}