#!/usr/bin/env python3
"""Read-only static scan of an Agent Skill directory.

Prints a JSON report. Does not import, spawn, or follow instructions inside the skill.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TEXT_SUFFIXES = {
    ".md", ".txt", ".py", ".sh", ".js", ".mjs", ".cjs", ".ts", ".tsx",
    ".json", ".yml", ".yaml", ".toml", ".ini", ".html", ".css", ".svg",
}

# Categories describe capability, not intent. Keep samples short and non-operational.
RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("command_execution", "high", re.compile(
        r"\b(subprocess|os\.system|child_process|exec\(|eval\(|bash\s+-lc|powershell|curl\s+[^\n]{0,40}\|\s*(ba)?sh)\b",
        re.I,
    )),
    ("network", "medium", re.compile(
        r"\b(curl|wget|requests\.|fetch\(|http://|https://|WebFetch|socket)\b",
        re.I,
    )),
    ("secrets", "high", re.compile(
        r"(api[_-]?key|secret|token|password|credential|\.env\b|id_rsa|BEGIN [A-Z ]*PRIVATE KEY)",
        re.I,
    )),
    ("destructive_fs", "high", re.compile(
        r"(rm\s+-rf|shutil\.rmtree|Remove-Item\s+-Recurse|unlink\(|os\.remove)",
        re.I,
    )),
    ("install", "medium", re.compile(
        r"\b(pip3?\s+install|npm\s+install|pnpm\s+add|apt(-get)?\s+install|brew\s+install)\b",
        re.I,
    )),
    ("instruction_override", "high", re.compile(
        r"(ignore (all |any )?(previous|prior) instructions|you are now|system prompt|jailbreak)",
        re.I,
    )),
    ("obfuscated_exec", "high", re.compile(
        r"(base64\s+-d|atob\(|decode\([^\n]{0,40}exec|exec\(base64)",
        re.I,
    )),
]

FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
FIELD = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$", re.M)


def read_text(path: Path, limit: int = 400_000) -> str:
    data = path.read_bytes()[:limit]
    return data.decode("utf-8", errors="replace")


def parse_frontmatter(text: str) -> dict[str, str]:
    match = FRONTMATTER.match(text)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for key, value in FIELD.findall(match.group(1)):
        fields[key] = value.strip().strip("\"'")
    return fields


def clip(line: str, width: int = 160) -> str:
    line = " ".join(line.strip().split())
    if len(line) <= width:
        return line
    return line[: width - 1] + "…"


def scan(root: Path) -> dict:
    root = root.resolve()
    files: list[dict] = []
    findings: list[dict] = []
    skill_md = root / "SKILL.md"
    frontmatter: dict[str, str] = {}
    headings: list[str] = []

    if skill_md.is_file():
        text = read_text(skill_md)
        frontmatter = parse_frontmatter(text)
        headings = [line for line in text.splitlines() if re.match(r"^#{1,3} ", line)]

    seen: set[tuple[str, int, str]] = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        info = {
            "path": rel,
            "bytes": path.stat().st_size,
            "text": path.suffix.lower() in TEXT_SUFFIXES,
        }
        files.append(info)
        if not info["text"] or info["bytes"] > 1_000_000:
            continue
        text = read_text(path)
        for category, severity, pattern in RULES:
            for match in pattern.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                key = (rel, line_no, category)
                if key in seen:
                    continue
                seen.add(key)
                line = text.splitlines()[line_no - 1] if text.splitlines() else ""
                findings.append({
                    "category": category,
                    "severity": severity,
                    "file": rel,
                    "line": line_no,
                    "sample": clip(line),
                })
                if sum(1 for item in findings if item["file"] == rel and item["category"] == category) >= 5:
                    break

    by_category: dict[str, int] = {}
    for item in findings:
        by_category[item["category"]] = by_category.get(item["category"], 0) + 1

    return {
        "root": str(root),
        "skill_md_present": skill_md.is_file(),
        "frontmatter": frontmatter,
        "headings": headings,
        "file_count": len(files),
        "files": files,
        "finding_counts": by_category,
        "findings": findings,
        "note": "Static text match only. A hit is a capability to review, not proof of malice. No skill code was executed.",
    }


def protected_roots() -> list[Path]:
    skill = Path(__file__).resolve().parents[1]
    roots = [skill]
    if skill.parent.name == "skills":
        repo = skill.parent.parent
        if (repo / ".git").is_dir() or (repo / ".git").is_file():
            roots.append(repo)
    return roots


def refuse_inside_repo(path: Path) -> None:
    resolved = path.resolve()
    for root in protected_roots():
        if resolved == root or root in resolved.parents:
            raise SystemExit(f"输出不能写进 skill 目录或其所在源仓库：{resolved}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Static read-only scan of a skill directory")
    parser.add_argument("skill_dir", type=Path)
    parser.add_argument("--out", type=Path, help="Write the JSON report outside this skill repository")
    args = parser.parse_args()
    if args.out:
        refuse_inside_repo(args.out)
    if not args.skill_dir.is_dir():
        print(json.dumps({"error": "not a directory", "path": str(args.skill_dir)}, ensure_ascii=False))
        return 2
    report = scan(args.skill_dir)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.out:
        args.out.write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
