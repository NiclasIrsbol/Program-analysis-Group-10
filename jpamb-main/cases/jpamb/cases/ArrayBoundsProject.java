package jpamb.cases;

import static jpamb.utils.Tag.TagType.*;

import jpamb.utils.*;

public class ArrayBoundsProject {

  /*
  * Each pair isolates a different relational question:
  *
  * 1. Transitive bounds:
  *    Can the analysis combine relations such as i <= j and
  *    j < array.length to prove that i is also in bounds?
  *
  * 2. Off-by-one bounds:
  *    Can the analysis distinguish i < array.length from
  *    i <= array.length?
  *
  * 3. Difference relations:
  *    Can the analysis use a fixed relation between two indices,
  *    such as j - i = 2, to prove an access safe?
  *
  * 4. Array reassignment:
  *    Does a relation between an index and array.length become invalid
  *    when the array is replaced by a differently sized array?
  *
  * 5. Derived indices:
  *    Can the analysis propagate bounds through assignments such as
  *    j = i + 1?
  *
  * 6. Relations between array lengths:
  *    Can the analysis reason about the lengths of two arrays and
  *    invalidate that relation when one array changes?
  *
  * 7. Copied array lengths:
  *    Can the analysis relate a variable storing array.length back to
  *    the array, and detect when that copied length becomes stale?
  *
  * 8. Control-flow joins:
  *    Can the analysis merge information from multiple branches while
  *    keeping only relations that hold on every incoming path?
  *
  * 9. Nested refinement:
  *    Can relational facts learned in an outer branch be combined with
  *    facts learned in an inner branch to prove an access safe?
  *
  * 10. Index equality:
  *     Can bounds information be transferred between equal index
  *     variables, and is that relation invalidated when one changes?
  */

  // Pair 1: Transitive relation between two indices and array length

  @Case("(1, 2) -> ok")
  @Case("(2, 3) -> ok")
  @Tag({ARRAY})
  public static int safeTransitiveBound(int i, int j) {
    int[] array = {10, 20, 30, 40};

    if (i >= 0 && i <= j && j < array.length) {
      return array[i];
    }

    return 0;
  }

  @Case("(2, 1) -> ok")
  @Case("(4, 3) -> out of bounds")
  @Tag({ARRAY})
  public static int unsafeTransitiveBound(int i, int j) {
    int[] array = {10, 20, 30, 40};

    if (i >= 0 && j < array.length && j <= i) {
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

// Pair 3: Fixed difference between two indices

  @Case("(1, 3) -> ok")
  @Case("(2, 4) -> ok")
  @Tag({ARRAY})
  public static int safeIndexDifference(int i, int j) {
    int[] array = {10, 20, 30, 40, 50};

    if (j >= 2 && j < array.length && j - i == 2) {
      return array[i];
    }

    return 0;
  }

  @Case("(1, 3) -> ok")
  @Case("(-1, 1) -> out of bounds")
  @Tag({ARRAY})
  public static int unsafeIndexDifference(int i, int j) {
    int[] array = {10, 20, 30, 40, 50};

    if (j >= 0 && j < array.length && j - i == 2) {
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
  // Pair 9: Relational information refined through nested guards

  @Case("(1, 2) -> ok")
  @Case("(2, 3) -> ok")
  @Tag({ARRAY})
  public static int safeNestedRefinement(int i, int j) {
    int[] array = {10, 20, 30, 40};

    if (i >= 0 && i < array.length - 1) {
      if (j == i + 1) {
        return array[j];
      }
    }

    return 0;
  }

  @Case("(1, 2) -> ok")
  @Case("(3, 4) -> out of bounds")
  @Tag({ARRAY})
  public static int unsafeNestedRefinement(int i, int j) {
    int[] array = {10, 20, 30, 40};

    if (i >= 0 && i < array.length) {
      if (j == i + 1) {
        return array[j];
      }
    }

    return 0;
  }

  // Pair 10: Alias relation between two indices

  @Case("(1) -> ok")
  @Case("(3) -> ok")
  @Tag({ARRAY})
  public static int safeAliasGuard(int i) {
    int[] array = {10, 20, 30, 40};
    int j = i;

    if (j >= 0 && j < array.length) {
        return array[i];
    }

    return 0;
  }

  @Case("(1) -> ok")
  @Case("(4) -> out of bounds")
  @Tag({ARRAY})
  public static int unsafeAliasGuard(int i) {
    int[] array = {10, 20, 30, 40};

    int j = i;
    j = j - 1;

    if (j >= 0 && j < array.length) {
      return array[i];
    }

    return 0;
  }

}