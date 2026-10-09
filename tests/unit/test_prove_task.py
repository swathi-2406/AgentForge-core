"""prove_task.gaps: finds real list jumps, ignores references and inline alternatives."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("prove_task", Path(__file__).resolve().parents[2] / "scripts" / "prove_task.py")
prove_task = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prove_task)


def jumps(text):
    return [(b, a) for _, b, a, _ in prove_task.gaps(text)]


@pytest.mark.parametrize("text, expected", [
    ("(i) one; (ii) two; (iii) three; (iv) four; and (vi) six. See clause (vi) above.", [("iv", "vi")]),
    ("(a) one (b) two (d) four", [("b", "d")]),
    ("(a) x; (b) y; (c) z; or (d) w; and (e) v", []),
    ("account number(s), identification number(s)", []),
    ("Test 3.3(j) - 1 and Test 3.3(v) - 1", []),
    ("no later than (x) three days or (y) five days", []),
    ("(l) a; (m) b, permitted by Section 7.06(i)(iii); (n) c", []),
    ("(a) a; (b) b; as in clauses (a), (b) and (d) above; (c) c", []),
    ("(g) g; (h) h; (i) i; (j) j", []),
    ("(viii) a; (ix) b; (x) c", []),
    ("(a) one, as required by Section 4.1. (b) two. (c) three", []),
    ("(a) one under Article II. (b) two (c) three", []),
    ("(i) a; (ii) b; (iii) c; (iv) d under Section 2.05. (vi) f", [("iv", "vi")]),
    ("(i) a, (ii) b, (iii) c, (iv) d, in the last sentence of Section 2.14(a) and (vi) f", [("iv", "vi")]),
    ("(a) a; (b) b, as in Section 2.14(a) and (c), (c) c", []),
])
def test_gaps(text, expected):
    assert jumps(text) == expected