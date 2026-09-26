"""Cheap L0 classification: language and binary-ness from names only.

No file bodies are read here. Unknown extensions are reported as
``Language.OTHER`` text so unusual-but-relevant files still enter the
inventory; well-known binary extensions are flagged without inspection.
"""

from __future__ import annotations

from harness.contracts.repository import Language

LANG_BY_EXT: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".pyi": Language.PYTHON,
    ".pyw": Language.PYTHON,
    ".js": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT,
    ".mts": Language.TYPESCRIPT,
    ".cts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".go": Language.GO,
    ".rs": Language.RUST,
    ".java": Language.JAVA,
    ".kt": Language.KOTLIN,
    ".kts": Language.KOTLIN,
    ".swift": Language.SWIFT,
    ".c": Language.C,
    ".h": Language.C,
    ".cpp": Language.CPP,
    ".cc": Language.CPP,
    ".cxx": Language.CPP,
    ".hpp": Language.CPP,
    ".hh": Language.CPP,
    ".cs": Language.CSHARP,
    ".m": Language.OBJECTIVE_C,
    ".mm": Language.OBJECTIVE_C,
    ".rb": Language.RUBY,
    ".rake": Language.RUBY,
    ".php": Language.PHP,
    ".sh": Language.SHELL,
    ".bash": Language.SHELL,
    ".zsh": Language.SHELL,
    ".fish": Language.SHELL,
    ".ps1": Language.POWERSHELL,
    ".psm1": Language.POWERSHELL,
    ".lua": Language.LUA,
    ".pl": Language.PERL,
    ".pm": Language.PERL,
    ".scala": Language.SCALA,
    ".sc": Language.SCALA,
    ".dart": Language.DART,
    ".html": Language.HTML,
    ".htm": Language.HTML,
    ".css": Language.CSS,
    ".scss": Language.SCSS,
    ".sass": Language.SCSS,
    ".less": Language.CSS,
    ".sql": Language.SQL,
    ".proto": Language.PROTOBUF,
    ".graphql": Language.GRAPHQL,
    ".gql": Language.GRAPHQL,
    ".json": Language.JSON,
    ".jsonc": Language.JSON,
    ".json5": Language.JSON,
    ".yml": Language.YAML,
    ".yaml": Language.YAML,
    ".toml": Language.TOML,
    ".xml": Language.XML,
    ".xhtml": Language.XML,
    ".plist": Language.XML,
    ".md": Language.MARKDOWN,
    ".markdown": Language.MARKDOWN,
    ".mdx": Language.MARKDOWN,
    ".rst": Language.MARKDOWN,
    ".txt": Language.TEXT,
    ".ini": Language.INI,
    ".cfg": Language.INI,
    ".conf": Language.INI,
}

LANG_BY_NAME: dict[str, Language] = {
    "dockerfile": Language.DOCKERFILE,
    "makefile": Language.MAKEFILE,
    "gnumakefile": Language.MAKEFILE,
    "cmakelists.txt": Language.CMAKE,
    "justfile": Language.MAKEFILE,
    ".justfile": Language.MAKEFILE,
    "rakefile": Language.RUBY,
    "gemfile": Language.RUBY,
    "vagrantfile": Language.RUBY,
    "brewfile": Language.RUBY,
    ".gitignore": Language.INI,
    ".dockerignore": Language.INI,
    ".editorconfig": Language.INI,
    ".env.example": Language.INI,
}

# Extensions that are treated as binary without reading a body.
BINARY_EXTS: frozenset[str] = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff", ".icns",
        ".svgz", ".pdf", ".psd", ".ai", ".sketch",
        ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tgz",
        ".jar", ".war", ".class", ".dex",
        ".so", ".dylib", ".dll", ".exe", ".o", ".a", ".lib", ".obj",
        ".wasm", ".pyd", ".pyc", ".pyo",
        ".db", ".sqlite", ".sqlite3", ".mdb",
        ".mp3", ".mp4", ".mov", ".avi", ".mkv", ".wav", ".flac", ".webm",
        ".parquet", ".feather", ".arrow", ".h5", ".hdf5", ".npy", ".npz", ".pkl",
        ".bin", ".dat", ".iso", ".img", ".dmg", ".deb", ".rpm", ".apk", ".ipa",
        ".lockb",
    }
)


def classify_file(name: str) -> tuple[str, bool]:
    """Return ``(language, is_binary)`` for a file basename, never reading it."""
    lowered = name.lower()
    by_name = LANG_BY_NAME.get(lowered)
    if by_name is not None:
        return by_name.value, False
    _, ext = _split_ext(lowered)
    binary = ext in BINARY_EXTS
    language = LANG_BY_EXT.get(ext)
    if language is None:
        # Compound extension like ".d.ts" or ".test.js" still resolves to a language.
        language = _compound_language(lowered)
    if language is None:
        return Language.OTHER.value, binary
    return language.value, binary


def _split_ext(lowered: str) -> tuple[str, str]:
    stem, dot, ext = lowered.rpartition(".")
    if not dot:
        return lowered, ""
    return stem, f".{ext}"


def _compound_language(lowered: str) -> Language | None:
    for suffix, language in (
        (".d.ts", Language.TYPESCRIPT),
        (".test.js", Language.JAVASCRIPT),
        (".spec.js", Language.JAVASCRIPT),
        (".test.ts", Language.TYPESCRIPT),
        (".spec.ts", Language.TYPESCRIPT),
        (".min.js", Language.JAVASCRIPT),
        (".d.ts.map", Language.JSON),
        (".map", Language.JSON),
    ):
        if lowered.endswith(suffix):
            return language
    return None
