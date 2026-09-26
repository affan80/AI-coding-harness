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

class DiscoveryEngine:
    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path).resolve()

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

    def text_search(self, query: str, is_regex: bool = False) -> list[SearchResult]:
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
            return self._fallback_search(query, is_regex)

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
        return results

    def symbol_search(self, symbol: str) -> list[SearchResult]:
        """
        Looks for symbol declarations.
        Fallback to text_search if language isn't explicitly parsed.
        """
        results = []

        # We can use regex for basic symbol declaration (class/def/function)
        regex_query = rf"(class|def|function|const|let|var|type|interface)\s+{symbol}\b"

        # Find declaration candidates
        candidates = self.text_search(regex_query, is_regex=True)
        if candidates:
            for c in candidates:
                c.match_reason = f"Symbol declaration for '{symbol}'"
            results.extend(candidates)
        else:
            # Fallback exact text match
            fallback = self.text_search(symbol, is_regex=False)
            for c in fallback:
                c.match_reason = f"Possible symbol '{symbol}'"
            results.extend(fallback)

        return results

    def reference_lookup(self, symbol: str) -> list[SearchResult]:
        """
        Looks for symbol usage/references/callers.
        """
        results = self.text_search(symbol, is_regex=False)
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
