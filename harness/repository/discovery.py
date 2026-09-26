import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SearchResult:
    path: str
    line_start: int
    line_end: int
    match_reason: str
    truncation_status: bool
    context: str
    evidence_ref: str = ""


@dataclass
class TreeResult:
    """Bounded, ignore-aware repository tree (issue #42)."""

    entries: list[str]
    total_files: int
    truncated: bool
    truncation_reasons: list[str]
    evidence_ref: str = ""

class DiscoveryEngine:
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()
        self._search_seq = 0

    def _next_evidence_ref(self) -> str:
        self._search_seq += 1
        return f"search-{self._search_seq:04d}"

    def bounded_tree(self, max_entries: int = 200) -> TreeResult:
        """Ignore-aware repository tree with hard entry and depth bounds.

        Filtering (built-in exclusions, .gitignore, traversal caps) is
        delegated to the inventory builder so tree output and the candidate
        inventory can never disagree (issue #42; PRD §§12.4, 13).
        """
        from harness.repository.inventory import InventoryLimits, build_inventory

        limits = InventoryLimits(max_files=max_entries)
        result = build_inventory(self.repo_path, limits)
        entries = [f.path for f in result.files]
        return TreeResult(
            entries=entries,
            total_files=len(result.files),
            truncated=result.truncated,
            truncation_reasons=list(result.truncation_reasons),
            evidence_ref=self._next_evidence_ref(),
        )

    @staticmethod
    def _cap_results(
        results: list["SearchResult"], max_results: int, evidence_ref: str
    ) -> list["SearchResult"]:
        """Bound results; the last kept entry reports the truncation."""
        if len(results) > max_results:
            kept = results[:max_results]
            kept[-1].truncation_status = True
            kept[-1].context = _cap_context(kept[-1].context)
        else:
            kept = results
        for item in kept:
            item.evidence_ref = evidence_ref
        return kept

    def _run_cmd(self, cmd: list[str], check_exit: bool = False) -> str:
        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                check=check_exit
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            return e.stdout

    def _fallback_search(self, query: str, is_regex: bool) -> list[SearchResult]:
        # Fallback if git grep doesn't work (e.g. not a git repo)
        results = []
        if is_regex:
            pattern = re.compile(query, re.IGNORECASE)
        else:
            pattern = re.compile(re.escape(query), re.IGNORECASE)
        for root, dirs, files in os.walk(str(self.repo_path)):
            if '.git' in dirs:
                dirs.remove('.git')
            if 'node_modules' in dirs:
                dirs.remove('node_modules')
            for file in files:
                filepath = Path(root) / file
                try:
                    lines = filepath.read_text(encoding='utf-8', errors='ignore').splitlines()
                    for idx, line in enumerate(lines):
                        if pattern.search(line):
                            results.append(SearchResult(
                                path=str(filepath.relative_to(self.repo_path)),
                                line_start=idx + 1,
                                line_end=idx + 1,
                                match_reason=f"Text match for '{query}'",
                                truncation_status=False,
                                context=line.strip()
                            ))
                except Exception:
                    pass
        return results

    def text_search(
        self, query: str, is_regex: bool = False, *, max_results: int = 50
    ) -> list[SearchResult]:
        evidence_ref = self._next_evidence_ref()
        results = []
        # Attempt to use git grep since it respects gitignore natively and is commonly available
        cmd = ["git", "grep", "--untracked", "-n", "-i"]
        if is_regex:
            cmd.append("-E")
        else:
            cmd.append("-F")
        cmd.append(query)

        output = self._run_cmd(cmd)
        if not output.strip():
            # Try fallback search
            return self._cap_results(
                self._fallback_search(query, is_regex), max_results, evidence_ref
            )

        for line in output.splitlines():
            if not line.strip():
                continue
            parts = line.split(":", 2)
            if len(parts) >= 3:
                path, line_num, content = parts[0], int(parts[1]), parts[2]
                results.append(SearchResult(
                    path=path,
                    line_start=line_num,
                    line_end=line_num,
                    match_reason=f"Text match for '{query}'",
                    truncation_status=False,
                    context=content.strip()
                ))
        return self._cap_results(results, max_results, evidence_ref)

    def symbol_search(
        self, symbol: str, *, max_results: int = 50
    ) -> list[SearchResult]:
        """
        Looks for symbol declarations.
        Fallback to text_search if language isn't explicitly parsed.
        """
        results = []

        # We can use regex for basic symbol declaration (class/def/function)
        regex_query = rf"(class|def|function|const|let|var|type|interface)\s+{symbol}\b"

        # Find declaration candidates
        candidates = self.text_search(regex_query, is_regex=True,
                                      max_results=max_results)
        if candidates:
            for c in candidates:
                c.match_reason = f"Symbol declaration for '{symbol}'"
            results.extend(candidates)
        else:
            # Fallback exact text match
            fallback = self.text_search(symbol, is_regex=False,
                                        max_results=max_results)
            for c in fallback:
                c.match_reason = f"Possible symbol '{symbol}'"
            results.extend(fallback)

        return results

    def reference_lookup(
        self, symbol: str, *, max_results: int = 50
    ) -> list[SearchResult]:
        """
        Looks for symbol usage/references/callers.
        """
        results = self.text_search(symbol, is_regex=False, max_results=max_results)
        for res in results:
            res.match_reason = f"Reference to '{symbol}'"
        return results

    def find_tests_for_source(self, source_path: str) -> list[SearchResult]:
        """
        Map tests to source using paths, imports, names.
        """
        results = []
        path_obj = Path(source_path)
        name = path_obj.stem

        # Test conventions
        test_queries = [f"test_{name}", f"{name}.test", f"{name}.spec", f"{name}_test"]

        # Search repository for files matching these patterns
        cmd = ["git", "ls-files", "-c", "-o", "--exclude-standard"]
        files_output = self._run_cmd(cmd)
        files = [f for f in files_output.splitlines() if f.strip()]

        for f in files:
            for q in test_queries:
                if q in f:
                    results.append(SearchResult(
                        path=f,
                        line_start=1,
                        line_end=1,
                        match_reason=f"Test file for '{source_path}' based on naming convention",
                        truncation_status=False,
                        context=f
                    ))

        # Also check for imports in test directories
        if not results:
            cmd = ["git", "grep", "--untracked", "-n", "-i", name]
            output = self._run_cmd(cmd)
            for line in output.splitlines():
                if "test" in line.lower() and name in line:
                    parts = line.split(":", 2)
                    if len(parts) >= 3:
                        results.append(SearchResult(
                            path=parts[0],
                            line_start=int(parts[1]),
                            line_end=int(parts[1]),
                            match_reason=f"Test reference import/usage of '{name}'",
                            truncation_status=False,
                            context=parts[2].strip()
                        ))

        return results

    def extract_dependencies(self) -> dict[str, Any]:
        """
        Extract lightweight dependency relationships for supported languages.
        """
        dependencies = {}

        # Python
        if (self.repo_path / "pyproject.toml").exists():
            dependencies["python_pyproject"] = self._read_file(self.repo_path / "pyproject.toml")
        if (self.repo_path / "requirements.txt").exists():
            dependencies["python_requirements"] = self._read_file(
                self.repo_path / "requirements.txt"
            )

        # Node
        if (self.repo_path / "package.json").exists():
            try:
                pkg = json.loads(self._read_file(self.repo_path / "package.json"))
                dependencies["node_dependencies"] = pkg.get("dependencies", {})
                dependencies["node_dev_dependencies"] = pkg.get("devDependencies", {})
            except json.JSONDecodeError:
                dependencies["node_package_json"] = "Invalid JSON"

        return dependencies

    def _read_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding='utf-8')
        except Exception:
            return ""


def _cap_context(context: str, limit: int = 200) -> str:
    return context if len(context) <= limit else context[: limit - 3] + "..."
