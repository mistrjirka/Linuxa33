from __future__ import annotations

import re


class ShellContractError(RuntimeError):
    pass


def function_span(text: str, name: str) -> tuple[int, int, str]:
    lines = text.splitlines(keepends=True)
    start_re = re.compile(rf"^[ \t]*{re.escape(name)}[ \t]*\([ \t]*\)[ \t]*\{{[ \t]*(?:#.*)?(?:\n)?$")
    starts = [i for i, line in enumerate(lines) if start_re.match(line)]
    if len(starts) != 1:
        raise ShellContractError(f"expected one {name}(), found {len(starts)}")
    close_re = re.compile(r"^[ \t]*\}[ \t]*(?:#.*)?(?:\n)?$")
    for end in range(starts[0] + 1, len(lines)):
        if close_re.match(lines[end]):
            return starts[0], end + 1, "".join(lines[starts[0]:end + 1])
    raise ShellContractError(f"unterminated {name}()")


def replace_function(text: str, name: str, replacement: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    start, end, original = function_span(text, name)
    lines[start:end] = [replacement]
    patched = "".join(lines)
    if function_span(patched, name)[2] != replacement:
        raise ShellContractError(f"{name}() replacement did not round-trip")
    return patched, original


def root_consumption_mode(wait: str) -> str:
    calls = re.findall(r"\$\(\s*find_root_partition\s*\)|`\s*find_root_partition\s*`", wait)
    if len(calls) != 1:
        raise ShellContractError(f"expected one find_root_partition substitution, found {len(calls)}")
    patterns = [
        re.compile(r"(?:^|[;\n])\s*(?:local\s+|export\s+)?([A-Za-z_]\w*)\s*=\s*[\"']?\$\(\s*find_root_partition\s*\)[\"']?"),
        re.compile(r"(?:^|[;\n])\s*(?:local\s+|export\s+)?([A-Za-z_]\w*)\s*=\s*[\"']?`\s*find_root_partition\s*`[\"']?"),
    ]
    assigned = sorted({name for pattern in patterns for name in pattern.findall(wait)})
    if len(assigned) > 1:
        raise ShellContractError(f"ambiguous root assignments: {assigned}")
    if assigned:
        return f"assignment:{assigned[0]}"
    if re.search(r"-z\s+[\"']?\$\(\s*find_root_partition\s*\)[\"']?", wait):
        return "empty-test"
    return "direct-command-substitution"


def second_stage_calls(init2: str) -> list[tuple[str, int, str]]:
    specs = [
        ("run_hooks /hooks", r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*run_hooks\s+[\"']?/hooks[\"']?(?:$|[\s;&|])"),
        ("wait_root_partition", r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*wait_root_partition(?:$|[\s;&|])"),
        ("resize_root_partition", r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*resize_root_partition(?:$|[\s;&|])"),
        ("resize_root_filesystem", r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*resize_root_filesystem(?:$|[\s;&|])"),
        ("mount_root_partition", r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*mount_root_partition(?:$|[\s;&|])"),
        ("switch_root", r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*(?:exec\s+)?switch_root(?:$|[\s;&|])"),
    ]
    executable = [(n, line.strip()) for n, line in enumerate(init2.splitlines(), 1) if line.strip() and not line.lstrip().startswith("#")]
    result: list[tuple[str, int, str]] = []
    for label, source in specs:
        pattern = re.compile(source)
        matches = [(n, line) for n, line in executable if pattern.search(line)]
        if len(matches) != 1:
            raise ShellContractError(f"expected one executable {label}, found {matches}")
        result.append((label, *matches[0]))
    numbers = [number for _, number, _ in result]
    if numbers != sorted(numbers) or len(numbers) != len(set(numbers)):
        raise ShellContractError(f"invalid second-stage order: {result}")
    return result
