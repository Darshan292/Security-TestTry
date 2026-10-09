"""demo/envfile.py: local .env loading for the demo tools."""
import os
import subprocess

from demo.envfile import ROOT, load_env, permission_warning


def test_loads_without_overriding_and_handles_quotes(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text('# comment\nexport GROQ_TEST_A="gsk_abc"\nGROQ_TEST_B=plain # trailing\nGROQ_TEST_C=\nbad line\n'
                 "GROQ_TEST_D='x y'\n")
    monkeypatch.delenv("GROQ_TEST_A", raising=False)
    monkeypatch.setenv("GROQ_TEST_B", "from-shell")
    monkeypatch.delenv("GROQ_TEST_D", raising=False)
    loaded = load_env(f)
    assert os.environ["GROQ_TEST_A"] == "gsk_abc" and os.environ["GROQ_TEST_D"] == "x y"
    assert os.environ["GROQ_TEST_B"] == "from-shell"            # the environment wins over the file
    assert "GROQ_TEST_C" not in os.environ and sorted(loaded) == ["GROQ_TEST_A", "GROQ_TEST_D"]
    for k in ("GROQ_TEST_A", "GROQ_TEST_D"):
        monkeypatch.delenv(k)


def test_missing_file_is_fine(tmp_path):
    assert load_env(tmp_path / "nope.env") == []


def test_world_readable_env_is_flagged(tmp_path):
    f = tmp_path / ".env"
    f.write_text("X=1\n")
    f.chmod(0o644)
    assert "chmod 600" in permission_warning(f)
    f.chmod(0o600)
    assert permission_warning(f) is None


def test_env_file_is_git_ignored_and_example_is_not():
    def ignored(name):
        return subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", name]).returncode == 0
    assert ignored(".env") and not ignored(".env.example")
    assert (ROOT / ".env.example").read_text().count("GROQ_API_KEY=") == 1
    assert "gsk_" not in (ROOT / ".env.example").read_text()
