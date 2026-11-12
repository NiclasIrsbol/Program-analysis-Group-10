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

  // Pair 3: Strict lower bound vs missing lower bound

  @Case("(0) -> ok")
  @Tag({ARRAY})
  public static int strictLowerBound(int i) {
    int[] array = {10, 20, 30};

    if (i >= 0 && i < array.length) {
      return array[i];
    }

    return 0;
  }

  @Case("(0) -> ok")
  @Case("(-1) -> out of bounds")
  @Tag({ARRAY})
  public static int missingLowerBound(int i) {
    int[] array = {10, 20, 30};

    if (i < array.length) {
      return array[i];
    }

    return 0;
  }

  // Pair 4: Recheck after array reassignment vs stale index

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

  // Pair 5: Safe derived index vs unsafe derived index

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

  // Pair 6: Array length relation becomes stale after reassignment

  @Case("(1) -> ok")
  @Case("(2) -> ok")
  @Tag({ARRAY})
  public static int recheckArrayLengthRelation(int i) {
    int[] a = new int[5];
    int[] b = new int[3];

    if (a.length >= b.length) {
      a = new int[2];

      if (i >= 0 && i < b.length && i < a.length) {
        return a[i];
      }
    }

    return 0;
  }

  @Case("(1) -> ok")
  @Case("(2) -> out of bounds")
  @Tag({ARRAY})
  public static int staleArrayLengthRelation(int i) {
    int[] a = new int[5];
    int[] b = new int[3];

    if (a.length >= b.length) {
      a = new int[2];

      if (i >= 0 && i < b.length) {
        return a[i];
      }
    }

    return 0;
  }

  // Pair 7: Safe length copy vs stale length copy

  @Case("() -> ok")
  @Tag({ARRAY})
  public static int safeLengthCopy() {
    int[] array = new int[5];

    int n = array.length;
    int i = n - 1;

    if (i >= 0 && i < n) {
      return array[i];
    }

    return 0;
  }

  @Case("() -> out of bounds")
  @Tag({ARRAY})
  public static int staleLengthCopy() {
    int[] array = new int[5];

    int n = array.length;
    int i = n - 1;

    array = new int[2];

    if (i >= 0 && i < n) {
      return array[i];
    }

    return 0;
  }

  // Pair 8: Safe join vs unsafe join

  @Case("([I: 10, 20, 30], 0) -> ok")
  @Case("([I: 10, 20, 30], 1) -> ok")
  @Tag({ARRAY})
  public static int safeJoin(int[] array, int branch) {
    if (array.length < 2) {
      return 0;
    }

    int i;

    if (branch == 0) {
      i = array.length - 1;
    } else {
      i = array.length - 2;
    }

    return array[i];
  }

  @Case("([I: 10, 20, 30], 0) -> ok")
  @Case("([I: 10, 20, 30], 1) -> out of bounds")
  @Tag({ARRAY})
  public static int unsafeJoin(int[] array, int branch) {
    if (array.length == 0) {
      return 0;
    }

    int i;

    if (branch == 0) {
      i = array.length - 1;
    } else {
      i = array.length;
    }

    return array[i];
  }
}