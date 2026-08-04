#!/usr/bin/env bash
set -Eeuo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE="$SCRIPT_DIR/make-u0i-direct-root-function-recovery.sh"
EXPECTED_SOURCE_BLOB="5081588ef9c10dec5aab74cead7adc443191c3df"
TMP="$SCRIPT_DIR/.make-u0i-direct-root-function-v2-$$.$RANDOM.sh"

for command in bash cp rm python3 git grep; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
[[ -f "$SOURCE" ]] || {
    echo "Missing U0i direct-root builder: $SOURCE" >&2
    exit 1
}

SOURCE_BLOB="$(git -C "$REPO_ROOT" hash-object "$SOURCE")"
if [[ "$SOURCE_BLOB" != "$EXPECTED_SOURCE_BLOB" ]]; then
    echo "REFUSING: U0i core builder changed unexpectedly" >&2
    echo "expected_blob=$EXPECTED_SOURCE_BLOB" >&2
    echo "actual_blob=$SOURCE_BLOB" >&2
    exit 1
fi

cleanup() { rm -f "$TMP"; }
trap cleanup EXIT
cp "$SOURCE" "$TMP"

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

anchor = 'if "find_root_partition" not in wait_text:'
anchor_pos = text.find(anchor)
if anchor_pos < 0:
    raise SystemExit("root-consumer validation anchor is missing")
sequence_pos = text.find("sequence = [", anchor_pos)
if sequence_pos < 0:
    raise SystemExit("second-stage sequence validator is missing")

# Replace the whole old consumer-shape block semantically. Do not depend on its
# local variable name or on an exact multiline copy of the source.
consumer_markers = [
    text.find("patterns = [", anchor_pos, sequence_pos),
    text.find("assignment_patterns = [", anchor_pos, sequence_pos),
    text.find("substitutions = len(", anchor_pos, sequence_pos),
]
consumer_starts = [position for position in consumer_markers if position >= 0]
if len(consumer_starts) != 1:
    raise SystemExit(
        f"expected one root-consumer validator start, found {consumer_starts}"
    )
consumer_start = consumer_starts[0]
consumer_replacement = r'''substitutions = len(re.findall(
    r"\$\(\s*find_root_partition\s*\)|`\s*find_root_partition\s*`",
    wait_text,
))
if substitutions != 1:
    raise SystemExit(
        "expected exactly one find_root_partition command substitution in "
        f"wait_root_partition, found {substitutions}"
    )
assignment_patterns = [
    re.compile(
        r"(?:^|[;\n])\s*(?:local\s+|export\s+)?"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[\"']?"
        r"\$\(\s*find_root_partition\s*\)[\"']?"
    ),
    re.compile(
        r"(?:^|[;\n])\s*(?:local\s+|export\s+)?"
        r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[\"']?"
        r"`\s*find_root_partition\s*`[\"']?"
    ),
]
assigned = sorted({
    name
    for pattern in assignment_patterns
    for name in pattern.findall(wait_text)
})
if len(assigned) > 1:
    raise SystemExit(
        f"ambiguous wait_root_partition assignments from find_root_partition: {assigned}"
    )
if assigned:
    consumption_mode = f"assignment:{assigned[0]}"
elif re.search(
    r"-z\s+[\"']?\$\(\s*find_root_partition\s*\)[\"']?",
    wait_text,
):
    consumption_mode = "empty-test"
else:
    consumption_mode = "direct-command-substitution"

'''
text = text[:consumer_start] + consumer_replacement + text[sequence_pos:]

old_report = 'print(f"wait_root_assignment_variable={root_variable}")'
new_report = 'print(f"wait_root_consumption_mode={consumption_mode}")'
if text.count(old_report) == 1:
    text = text.replace(old_report, new_report, 1)
elif text.count(new_report) != 1:
    raise SystemExit("root-consumption report line is missing or ambiguous")

# Replace the textual token-order check with an executable-line check. This
# avoids comments, diagnostics, or function names being mistaken for calls.
sequence_start = text.find("sequence = [", anchor_pos)
sequence_end_marker = 'print(f"original_find_root_sha256='
sequence_end = text.find(sequence_end_marker, sequence_start)
if sequence_start < 0 or sequence_end < 0:
    raise SystemExit("cannot locate complete second-stage order validator")
sequence_replacement = r'''def first_executable(lines, pattern, label):
    matches = []
    for number, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if pattern.search(stripped):
            matches.append((number, stripped))
    if len(matches) != 1:
        raise SystemExit(f"expected one executable {label} call, found {matches}")
    return matches[0]

init2_lines = init2.splitlines()
commands = [
    (
        "run_hooks /hooks",
        re.compile(
            r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*run_hooks\s+"
            r"[\"']?/hooks[\"']?(?:$|[\s;&|])"
        ),
    ),
    (
        "wait_root_partition",
        re.compile(
            r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*wait_root_partition"
            r"(?:$|[\s;&|])"
        ),
    ),
    (
        "resize_root_partition",
        re.compile(
            r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*resize_root_partition"
            r"(?:$|[\s;&|])"
        ),
    ),
    (
        "resize_root_filesystem",
        re.compile(
            r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*resize_root_filesystem"
            r"(?:$|[\s;&|])"
        ),
    ),
    (
        "mount_root_partition",
        re.compile(
            r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*mount_root_partition"
            r"(?:$|[\s;&|])"
        ),
    ),
    (
        "switch_root",
        re.compile(
            r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*(?:exec\s+)?switch_root"
            r"(?:$|[\s;&|])"
        ),
    ),
]
ordered_calls = [
    (label, *first_executable(init2_lines, pattern, label))
    for label, pattern in commands
]
line_numbers = [number for _, number, _ in ordered_calls]
if line_numbers != sorted(line_numbers) or len(set(line_numbers)) != len(line_numbers):
    raise SystemExit(f"second-stage executable order is wrong: {ordered_calls}")
for label, number, command in ordered_calls:
    print(f"second_stage_call={label} line={number} text={command}")

'''
text = text[:sequence_start] + sequence_replacement + text[sequence_end:]

banned = (
    "expected one wait_root_partition assignment",
    "wait_root_assignment_variable=",
    "root_variable",
    "run_hooks_sources_current_shell",
    "find_root_partition lacks cmdline_read",
)
for token in banned:
    if token in text:
        raise SystemExit(f"stale invalid runtime assumption remains: {token}")

required = (
    "wait_root_consumption_mode=",
    "expected one executable {label} call",
    "second-stage executable order is wrong",
    "base_tree_unchanged_except_init_functions=yes",
    "hardlink_topology_preserved=yes",
)
for token in required:
    if token not in text:
        raise SystemExit(f"required corrected validation token is missing: {token}")

path.write_text(text, encoding="utf-8")
PY

# Bash syntax checking does not parse Python here-doc bodies. Compile every
# quoted Python here-doc separately before executing the generated builder.
python3 - "$TMP" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines()
blocks = []
index = 0
while index < len(lines):
    if re.search(r"<<'PY'\s*(?:\|.*)?$", lines[index]):
        start = index + 1
        end = start
        while end < len(lines) and lines[end] != "PY":
            end += 1
        if end == len(lines):
            raise SystemExit(f"unterminated Python here-doc after line {index + 1}")
        source = "\n".join(lines[start:end]) + "\n"
        compile(source, f"{path}:python-heredoc-{len(blocks) + 1}", "exec")
        blocks.append((start + 1, end))
        index = end
    index += 1
if not blocks:
    raise SystemExit("no Python here-docs found in generated builder")
print(f"python_heredoc_compile_count={len(blocks)}")
PY

bash -n "$TMP"
if command -v shellcheck >/dev/null 2>&1; then
    shellcheck -S error "$TMP"
fi

grep -Fq 'wait_root_consumption_mode=' "$TMP"
! grep -Fq 'wait_root_assignment_variable=' "$TMP"
! grep -Fq 'root_variable' "$TMP"

bash "$TMP"
