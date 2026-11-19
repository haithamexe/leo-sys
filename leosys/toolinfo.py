"""Light helpers that classify what Claude is doing. No heavy imports: hooks use this on every tool call."""
import re

TEST_RE = re.compile(
    r"\b(pytest|py\.test|unittest|nosetests|tox|npm\s+(?:run\s+)?test|npm\s+t|yarn\s+test|pnpm\s+(?:run\s+)?test|"
    r"vitest|jest|mocha|go\s+test|cargo\s+test|dotnet\s+test|rspec|phpunit|ctest)\b")
PUSH_RE = re.compile(r"\bgit\s+push\b")
GIT_RE = re.compile(r"\bgit\s+\w+")

_KIND = {
    "Edit": "edit", "MultiEdit": "edit", "NotebookEdit": "edit", "Write": "write", "Read": "read",
    "Grep": "search", "Glob": "search", "WebSearch": "web", "WebFetch": "web", "Task": "agent", "Agent": "agent",
}


def tool_kind(tool, command=""):
    """Glyph key for a tool call, or None when there is no glyph for it."""
    if tool == "Bash":
        if TEST_RE.search(command or ""):
            return "test"
        if GIT_RE.search(command or ""):
            return "git"
        return "bash"
    return _KIND.get(tool or "")


def is_push(command):
    return bool(PUSH_RE.search(command or ""))


def _int(m, i=1):
    return int(m.group(i)) if m and m.group(i) else 0


def parse_test_output(text):
    """(passed, failed) from common test runners' summary lines, or None if nothing recognisable."""
    if not text:
        return None
    text = text[-6000:]
    # cargo test: "test result: ok. 12 passed; 1 failed; ..." (one per crate)
    cargo = re.findall(r"test result: \w+\. (\d+) passed; (\d+) failed", text)
    if cargo:
        return sum(int(p) for p, _ in cargo), sum(int(f) for _, f in cargo)
    # jest / vitest: "Tests:  1 failed, 5 passed, 6 total"
    m = re.search(r"Tests:\s+(?:(\d+) failed, )?(?:(\d+) skipped, )?(?:(\d+) passed, )?(\d+) total", text)
    if m:
        return _int(m, 3), _int(m, 1)
    # unittest: "Ran 30 tests" then OK or FAILED (failures=2, errors=1)
    m = re.search(r"Ran (\d+) tests?", text)
    if m:
        total = int(m.group(1))
        fm = re.search(r"FAILED \(([^)]*)\)", text)
        if fm:
            bad = sum(int(n) for n in re.findall(r"=(\d+)", fm.group(1)))
            return max(0, total - bad), bad
        if re.search(r"^OK\b", text, re.M):
            return total, 0
    # pytest: "5 passed, 1 failed, 2 errors in 0.3s"
    passed = re.findall(r"(\d+) passed", text)
    failed = re.findall(r"(\d+) (?:failed|errors?)\b", text)
    if passed or failed:
        return (int(passed[-1]) if passed else 0), sum(int(x) for x in failed[-2:]) if failed else 0
    return None
