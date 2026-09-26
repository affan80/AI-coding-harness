"""Issues #52/#53: file tools — reads, creates, minimal patches, hash guards."""


from harness.tools.files import (
    apply_patch,
    create_file,
    file_hash,
    read_file,
    read_range,
)


def _make_file(tmp_path, name="app.py", content="def one():\n    return 1\n"):
    path = tmp_path / name
    path.write_text(content)
    return path


def test_read_file_returns_content_hash_and_truncation_flag(tmp_path):
    path = _make_file(tmp_path)
    result = read_file(path)
    assert result.ok
    assert "def one()" in result.data["content"]
    assert result.data["sha256"] == file_hash(path)
    assert not result.truncated


def test_read_range_returns_numbered_lines(tmp_path):
    path = _make_file(tmp_path)
    result = read_range(path, 2, 2)
    assert result.ok
    assert result.data["lines"] == {2: "    return 1"}
    assert read_range(path, 5, 9).ok is False
    assert read_range(tmp_path / "nope.py", 1, 2).error_kind == "missing"


def test_create_file_fails_on_existing_path_without_overwriting(tmp_path):
    path = _make_file(tmp_path)
    before = path.read_text()
    result = create_file(path, "overwritten!")
    assert not result.ok
    assert result.error_kind == "conflict"
    assert path.read_text() == before  # current work untouched


def test_apply_patch_minimal_diff_applies_with_changed_lines(tmp_path):
    path = _make_file(tmp_path)
    diff = """--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 def one():
-    return 1
+    return 11
+
+def two():
+    return 2
"""
    result = apply_patch(path, diff, expected_old_hash=file_hash(path))
    assert result.ok, result.summary
    app = result.data["application"]
    assert app["old_sha256"] != app["new_sha256"]
    assert app["changed_lines"] == [2, 3, 4, 5]
    assert "return 11" in path.read_text()
    assert "def two()" in path.read_text()


def test_apply_patch_rejects_stale_hash_without_overwriting(tmp_path):
    path = _make_file(tmp_path)
    stale_hash = file_hash(path)
    path.write_text("def changed():\n    pass\n")  # newer work by someone else
    diff = """--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def one():
-    return 1
+    return 11
"""
    result = apply_patch(path, diff, expected_old_hash=stale_hash)
    assert not result.ok
    assert result.error_kind == "stale_hash"
    assert path.read_text() == "def changed():\n    pass\n"


def test_apply_patch_context_mismatch_fails_cleanly(tmp_path):
    path = _make_file(tmp_path, content="completely different\n")
    diff = """--- a/app.py
+++ b/app.py
@@ -1,2 +1,2 @@
 def one():
-    return 1
+    return 11
"""
    result = apply_patch(path, diff, expected_old_hash=file_hash(path))
    assert not result.ok
    assert result.error_kind == "conflict"
    assert path.read_text() == "completely different\n"
