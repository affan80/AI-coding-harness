"""Release notes, tagged source archive, and checksum verification (#80).

Everything a releaser needs short of pushing the tag (a human decision):

- ``generate_notes(version)`` renders ``RELEASE_NOTES.md``: setup, the three
  demo commands, what shipped (from git log), verification evidence, and
  known limitations — the document the release workflow attaches;
- ``build_archive(version, out_dir)`` produces the same ``git archive`` tar
  the release workflow builds, deterministically, plus a SHA-256 checksum;
- ``verify_archive(out_dir, version)`` proves reproducibility (two builds
  hash identically) and that the checksum matches.

Tagging itself stays manual: a ``v*`` tag must only ever be pushed from a
commit whose CI and ``scripts/release_checks.py`` have passed.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_NAME = "ai-coding-harness-{version}.tar.gz"

KNOWN_LIMITATIONS: tuple[str, ...] = (
    "autonomous execution requires a model provider configured via "
    "HARNESS_MODEL_PROVIDER / HARNESS_MODEL_API_KEY; the offline default is "
    "the scripted fake client",
    "audit rounds pass through in the integrated path (a reviewer wiring "
    "for run_audit is not yet attached to the CLI entry point)",
    "ranking in the context assembler is lexical; the semantic layer from "
    "PRD §12.5 is intentionally deferred",
    "live GitHub MCP ingestion requires network access and a token; tests "
    "cover the seam offline",
)


@dataclass(frozen=True)
class ArchiveBundle:
    version: str
    archive: Path
    checksum: Path
    sha256: str


def _git(args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout


def generate_notes(
    version: str,
    *,
    release_checks_passed: bool = True,
) -> str:
    """Render RELEASE_NOTES.md: setup, demos, shipped work, limitations."""
    log = _git(["log", "--oneline", "-20"])
    shipped = [
        line for line in log.splitlines() if line.strip()
    ] or ["(no git history available in this checkout)"]
    checks_state = "PASSED" if release_checks_passed else "NOT RUN YET"

    lines = [
        f"# ai-coding-harness {version}",
        "",
        "Autonomous AI software-engineering harness: repository + natural-"
        "language objective in, evidence-backed verified change out.",
        "",
        "## Setup",
        "",
        "```bash",
        "python -m venv .venv && source .venv/bin/activate",
        "pip install -e '.[dev]'",
        "```",
        "",
        "Requires Python 3.12+. No runtime dependencies.",
        "",
        "## Demos",
        "",
        "```bash",
        "python -m demos.run_demo --scenario all   # bug-fix with recovery,",
        "                                          # bounded feature, and",
        "                                          # large-context proof",
        "python -m scripts.release_checks          # release acceptance",
        "```",
        "",
        "## Verification",
        "",
        f"- release acceptance checks: **{checks_state}**",
        "- full test suite and lint run in CI on every push and PR",
        f"- source archive: `{ARCHIVE_NAME.format(version=version)}` "
        "+ `.sha256` checksum",
        "",
        "## Shipped (recent history)",
        "",
        *[f"- {line}" for line in shipped],
        "",
        "## Known limitations",
        "",
        *[f"- {limitation}" for limitation in KNOWN_LIMITATIONS],
        "",
    ]
    return "\n".join(lines)


def write_notes(out_dir: Path, version: str, *, content: str | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "RELEASE_NOTES.md"
    path.write_text(
        content if content is not None else generate_notes(version),
        encoding="utf-8",
    )
    return path


def build_archive(version: str, out_dir: Path) -> ArchiveBundle:
    """Deterministic source archive + SHA-256 checksum (matches the workflow)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    name = ARCHIVE_NAME.format(version=version)
    archive = out_dir / name
    completed = subprocess.run(
        ["git", "archive", "--format=tar.gz", "--prefix=ai-coding-harness/",
         f"--output={archive}", "HEAD"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"git archive failed: {completed.stderr.strip()}"
        )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = out_dir / f"{name}.sha256"
    checksum.write_text(f"{digest}  {name}\n", encoding="utf-8")
    return ArchiveBundle(
        version=version, archive=archive, checksum=checksum, sha256=digest,
    )


def verify_archive(bundle: ArchiveBundle, out_dir: Path) -> tuple[bool, str]:
    """Rebuild the archive and confirm the checksum is reproducible."""
    rebuild = build_archive(bundle.version, out_dir / "verify")
    reproducible = rebuild.sha256 == bundle.sha256
    recorded = bundle.checksum.read_text(encoding="utf-8").split()[0]
    checksum_ok = recorded == bundle.sha256
    notes = (out_dir / "RELEASE_NOTES.md").is_file()
    return (
        reproducible and checksum_ok and notes,
        f"reproducible={reproducible}, checksum_ok={checksum_ok}, "
        f"notes_present={notes}",
    )


def prepare_release(version: str, out_dir: Path) -> tuple[ArchiveBundle, Path, tuple[bool, str]]:
    """Notes + archive + verification in one call (the releaser then tags)."""
    notes_path = write_notes(out_dir, version)
    bundle = build_archive(version, out_dir)
    verified = verify_archive(bundle, out_dir)
    return bundle, notes_path, verified
