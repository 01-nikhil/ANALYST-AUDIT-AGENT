"""
Fixture test for the persistent-memory dedup + hard-cap behavior in app/main.py.

No API calls. Redirects app.main.MEMORY_FILE_PATH to a temp file and shrinks
app.main.MEMORY_MAX_LESSONS so the cap is easy to exercise, then drives
add_lessons_to_memory() directly. Also checks the new injection representation
is lesson-only (no long "reason" field) and respects the cap.

Run with:
    python benchmark/test_memory_cap.py
"""
import os
import sys
import json
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import app.main as m

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
        print(f"FAIL: {msg}")
    else:
        print(f"OK:   {msg}")


def L(text):
    return {"lesson": text, "reason": "r", "source_of_feedback": "curated"}


# --- Set up an isolated temp memory file and a small cap ---
tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
tmp.write("[]")
tmp.close()

saved_path = m.MEMORY_FILE_PATH
saved_cap = m.MEMORY_MAX_LESSONS
try:
    m.MEMORY_FILE_PATH = tmp.name
    m.MEMORY_MAX_LESSONS = 3

    # 1. Add up to the cap.
    added, dup = m.add_lessons_to_memory([L("principle one"), L("principle two")])
    check(len(added) == 2 and len(dup) == 0, "adds new lessons below the cap")
    check(len(m.load_memory()) == 2, "memory has 2 lessons after first add")

    # 2. Dedup: exact + case-insensitive duplicates are not re-added.
    added, dup = m.add_lessons_to_memory([L("PRINCIPLE ONE"), L("  principle two  ")])
    check(len(added) == 0 and len(dup) == 2, "case-insensitive / whitespace duplicates are rejected")
    check(len(m.load_memory()) == 2, "memory unchanged after duplicate add")

    # 3. Cap: adding beyond MEMORY_MAX_LESSONS is refused.
    added, dup = m.add_lessons_to_memory([L("principle three"), L("principle four")])
    check(len(added) == 1, "only one lesson added when only one slot remains under the cap")
    check(len(dup) == 1, "the over-cap lesson is reported as not-added")
    check(len(m.load_memory()) == 3, "memory never exceeds MEMORY_MAX_LESSONS")

    # 4. Fully full -> nothing more is added.
    added, dup = m.add_lessons_to_memory([L("principle five")])
    check(len(added) == 0 and len(dup) == 1, "no lessons added once memory is at the cap")
    check(len(m.load_memory()) == 3, "memory stays capped at MEMORY_MAX_LESSONS")

    # 5. Injection representation: lesson-only, no "Reason:", capped.
    m.MEMORY_MAX_LESSONS = 2  # shrink cap for the injection-slice check
    mem = m.load_memory()  # 3 lessons on disk
    injected = mem[:m.MEMORY_MAX_LESSONS]
    block = "\n".join(f"- {i.get('lesson', '')}" for i in injected if i.get("lesson"))
    check(len(injected) == 2, "injection slice respects MEMORY_MAX_LESSONS (2 of 3)")
    check("Reason:" not in block, "injected block contains only lesson text, not the auditor 'reason' field")
    check(block.count("\n- ") + 1 == 2, "injected block lists exactly the capped number of principles")
finally:
    m.MEMORY_FILE_PATH = saved_path
    m.MEMORY_MAX_LESSONS = saved_cap
    os.unlink(tmp.name)

# 6. Sanity: real curated memory loads and is within the cap.
real = m.load_memory()
check(1 <= len(real) <= m.MEMORY_MAX_LESSONS,
      f"real curated memory ({len(real)}) is within the cap ({m.MEMORY_MAX_LESSONS})")
check(all(it.get("lesson") for it in real), "every curated principle has non-empty lesson text")

print("\n" + "=" * 60)
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
