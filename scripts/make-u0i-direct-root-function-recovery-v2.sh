#!/usr/bin/env bash
set -Eeuo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$SCRIPT_DIR/make-u0i-direct-root-function-recovery.sh"
TMP="$SCRIPT_DIR/.make-u0i-direct-root-function-v2-$$.$RANDOM.sh"

for command in bash cp rm python3; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
[[ -f "$SOURCE" ]] || {
    echo "Missing U0i direct-root builder: $SOURCE" >&2
    exit 1
}

cleanup() { rm -f "$TMP"; }
trap cleanup EXIT
cp "$SOURCE" "$TMP"

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

old = '''assignment_patterns = [
    re.compile(r"(?:^|[;\\n])\\s*(?:local\\s+|export\\s+)?([A-Za-z_][A-Za-z0-9_]*)\\s*=\\s*[\\"']?\\$\\(\\s*find_root_partition\\s*\\)[\\"']?"),
    re.compile(r"(?:^|[;\\n])\\s*(?:local\\s+|export\\s+)?([A-Za-z_][A-Za-z0-9_]*)\\s*=\\s*[\\"']?`\\s*find_root_partition\\s*`[\\"']?"),
]
assigned = []
for pattern in assignment_patterns:
    assigned.extend(pattern.findall(wait_text))
assigned = sorted(set(assigned))
if len(assigned) != 1:
    raise SystemExit(f"expected one wait_root_partition assignment from find_root_partition, found {assigned}")
root_variable = assigned[0]
if root_variable not in wait_text:
    raise SystemExit("captured root variable is not used by wait_root_partition")
'''

new = '''substitutions = len(re.findall(r"\\$\\(\\s*find_root_partition\\s*\\)|`\\s*find_root_partition\\s*`", wait_text))
if substitutions != 1:
    raise SystemExit(f"expected exactly one find_root_partition command substitution in wait_root_partition, found {substitutions}")
assignment_patterns = [
    re.compile(r"(?:^|[;\\n])\\s*(?:local\\s+|export\\s+)?([A-Za-z_][A-Za-z0-9_]*)\\s*=\\s*[\\"']?\\$\\(\\s*find_root_partition\\s*\\)[\\"']?"),
    re.compile(r"(?:^|[;\\n])\\s*(?:local\\s+|export\\s+)?([A-Za-z_][A-Za-z0-9_]*)\\s*=\\s*[\\"']?`\\s*find_root_partition\\s*`[\\"']?"),
]
assigned = []
for pattern in assignment_patterns:
    assigned.extend(pattern.findall(wait_text))
assigned = sorted(set(assigned))
if len(assigned) > 1:
    raise SystemExit(f"ambiguous wait_root_partition assignments from find_root_partition: {assigned}")
if assigned:
    consumption_mode = f"assignment:{assigned[0]}"
elif re.search(r"-z\\s+[\\"']?\\$\\(\\s*find_root_partition\\s*\\)[\\"']?", wait_text):
    consumption_mode = "empty-test"
else:
    consumption_mode = "direct-command-substitution"
'''

old_report = 'print(f"wait_root_assignment_variable={root_variable}")'
new_report = 'print(f"wait_root_consumption_mode={consumption_mode}")'

if text.count(old) != 1:
    raise SystemExit(f"expected one assignment-only validator block, found {text.count(old)}")
if text.count(old_report) != 1:
    raise SystemExit(f"expected one assignment report line, found {text.count(old_report)}")

text = text.replace(old, new, 1)
text = text.replace(old_report, new_report, 1)
if "root_variable" in text:
    raise SystemExit("stale root_variable reference remained after validator patch")
path.write_text(text, encoding="utf-8")
PY

bash -n "$TMP"
bash "$TMP"
