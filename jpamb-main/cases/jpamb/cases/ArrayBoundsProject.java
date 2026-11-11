package jpamb.cases;

import static jpamb.utils.Tag.TagType.*;

import jpamb.utils.*;

public class ArrayBoundsProject {
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
}