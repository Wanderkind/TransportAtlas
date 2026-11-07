"""Offline tests for the optional, read-only third-party validator runner."""
from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import json
import subprocess
from types import SimpleNamespace
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate_external_gtfs.py"
SPEC = spec_from_file_location("validate_external_gtfs", SCRIPT)
validator = module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def test_pin_is_exactly_release_v8_0_1():
    assert validator.VERSION == "8.0.1"
    assert validator.JAR_SHA256 == "19293ddd9b6f954f216d4f12054bd8a3232921751c4484339e339764a91000e2"
    assert validator.JAR_URL.endswith("/v8.0.1/gtfs-validator-8.0.1-cli.jar")


def test_missing_or_tampered_jar_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        validator.verify_jar(tmp_path / "absent.jar")
    bad = tmp_path / "bad.jar"
    bad.write_bytes(b"not-verified")
    with pytest.raises(ValueError, match="Untrusted"):
        validator.verify_jar(bad)


def test_download_refuses_arbitrary_mirror(tmp_path):
    with pytest.raises(ValueError, match="Unexpected"):
        validator.download_validator(tmp_path / "x.jar", url="https://evil.example/file.jar")


def test_java_requirement_checked(monkeypatch):
    monkeypatch.setattr(validator.subprocess, "run", lambda *_a, **_kw: SimpleNamespace(returncode=0, stderr='openjdk version "17.0.12"', stdout=""))
    assert validator.java_major("java") == 17
    monkeypatch.setattr(validator.subprocess, "run", lambda *_a, **_kw: SimpleNamespace(returncode=0, stderr='openjdk version "11.0.2"', stdout=""))
    with pytest.raises(RuntimeError, match=r"17\+"):
        validator.java_major("java")


@pytest.mark.parametrize("banner,major", [
    ('Picked up JAVA_TOOL_OPTIONS: -Xmx512m 64\njava version "22.0.2" 2024-07-16', 22),
    ('java version "1.8.0_311"', 8),
    ('openjdk version "21" 2023-09-19', 21),
])
def test_java_version_ignores_banners_and_reads_legacy_scheme(monkeypatch, banner, major):
    monkeypatch.setattr(validator.subprocess, "run", lambda *_a, **_kw: SimpleNamespace(returncode=0, stderr=banner, stdout=""))
    if major < 17:
        with pytest.raises(RuntimeError, match=f"found {major}"):
            validator.java_major("java")
    else:
        assert validator.java_major("java") == major


def test_redirect_to_plain_http_is_refused_before_request():
    handler = validator._HttpsOnlyRedirects()
    with pytest.raises(RuntimeError, match="non-HTTPS"):
        handler.redirect_request(None, None, 302, "Found", {}, "http://objects.example/jar")


def _write_reports(p: Path, *, error_text="[]"):
    p.mkdir(parents=True, exist_ok=True)
    (p / "report.json").write_text('{"summary":{"validatorVersion":"8.0.1"},"notices":[{"code":"missing_recommended_file","severity":"WARNING"}]}', encoding="utf-8")
    (p / "system_errors.json").write_text(error_text, encoding="utf-8")
    (p / "report.html").write_text("<html>Test</html>", encoding="utf-8")


def test_reports_are_not_declared_error_free(tmp_path):
    _write_reports(tmp_path)
    status, note = validator.evaluate_reports(tmp_path, 0)
    assert status == "report_generated" and "no claim" in note
    # v8.0.1's real shapes: {"notices": []} when clean, notices listed otherwise.
    _write_reports(tmp_path, error_text='{"notices":[]}')
    assert validator.evaluate_reports(tmp_path, 0)[0] == "report_generated"
    _write_reports(tmp_path, error_text='{"notices":[{"code":"thread_execution_error","severity":"ERROR"}]}')
    assert validator.evaluate_reports(tmp_path, 0)[0] == "validator_failed"
    _write_reports(tmp_path, error_text='{"systemErrors":[{"error":"could not parse"}]}')
    assert validator.evaluate_reports(tmp_path, 0)[0] == "unverified"
    _write_reports(tmp_path, error_text="")
    assert validator.evaluate_reports(tmp_path, 0)[0] == "report_generated"
    assert validator.evaluate_reports(tmp_path, 1)[0] == "validator_failed"
    (tmp_path / "report.json").unlink()
    assert validator.evaluate_reports(tmp_path, 0)[0] == "unverified"


def test_run_one_creates_evidence_without_touching_input(tmp_path, monkeypatch):
    archive = tmp_path / "feed.zip"
    archive.write_bytes(b"immutable local feed")
    digest = validator.sha256(archive)
    item = {"source":"aspo","snapshot_id":"snap", "archive":str(archive),
            "sha256":digest,"retrieved_at":"2026-11-06T00:00:00Z"}
    seen=[]
    def fake_run(args, **_kwargs):
        seen.append(args)
        out=Path(args[args.index("-o")+1]); _write_reports(out)
        return SimpleNamespace(returncode=0, stdout="done", stderr="")
    monkeypatch.setattr(validator.subprocess,"run",fake_run)
    params=dict(jar=tmp_path/"validator.jar",java="java",date="2026-11-06",threads=2,heap_mb=4096,
                output_root=tmp_path/"reports",timeout_seconds=300,force=False)
    first=validator.run_one(item,**params)
    assert first["status"] == "report_generated"
    assert first["input_sha256"] == digest
    assert archive.read_bytes() == b"immutable local feed"
    assert len(seen) == 1
    assert "-svu" in seen[0]  # no network version check during validation
    second=validator.run_one(item,**params)
    assert second["reused_existing_report"] is True
    assert len(seen) == 1
    first_evidence = Path(first["report_directory"], "evidence.json").read_text(encoding="utf-8")
    third=validator.run_one(item,**{**params,"force":True})
    assert len(seen) == 2
    assert third["report_directory"] != first["report_directory"]
    # Each run's evidence sits beside its own report; the earlier record is preserved.
    assert json.loads(Path(third["report_directory"], "evidence.json").read_text(encoding="utf-8"))["report_directory"] == third["report_directory"]
    assert Path(first["report_directory"], "evidence.json").read_text(encoding="utf-8") == first_evidence


def test_run_one_failure_does_not_claim_success(tmp_path, monkeypatch):
    item = {"source":"arst","snapshot_id":"s","archive":str(tmp_path/"feed.zip"),
            "sha256":"abcd"*16,"retrieved_at":"2026-11-06T00:00Z"}
    monkeypatch.setattr(validator.subprocess,"run",lambda *_a,**_kw: SimpleNamespace(returncode=1,stdout="",stderr="bad"))
    r=validator.run_one(item,jar=tmp_path/"jar",java="java",date="2026-11-06",threads=1,
                         heap_mb=2048,output_root=tmp_path/"out",timeout_seconds=300,force=False)
    assert r["status"] == "validator_failed"
    assert Path(r["report_directory"]).joinpath("evidence.json").is_file()
