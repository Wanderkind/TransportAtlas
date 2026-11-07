"""Phase 7D2: archive-record hardening (B1 independent version anchor, B2 atomic no-clobber write).

Synthetic ZIPs in tmp_path; the only real-file test is read-only and skips if the user's
download is absent.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from transport_atlas import admin
from transport_atlas.archive_provenance import archive_problems, load_providers, sha1_file
from transport_atlas.official import catalog
from . import archive_fixtures as af
from . import history_fixtures as hf
from .test_phase7c_archive_provenance import CATALOG, SRC

ROOT = Path(__file__).resolve().parents[2]
REAL_DOWNLOAD = ROOT / 'data/downloads/archive/transitland/aspo/f-spqb-aspoolbia-b48388a26eeffbadde634b526dc3cb1acd8606ab.zip'
REAL_SHA1 = 'b48388a26eeffbadde634b526dc3cb1acd8606ab'


@pytest.fixture
def zip_path(tmp_path):
    path = tmp_path / 'downloads' / 'archived.zip'
    path.parent.mkdir(parents=True)
    shutil.copyfile(hf.build(tmp_path / 'zips')['v1'], path)
    return path


def kw(**extra):
    return {**dict(provider=af.BROWSER_PROVIDER, feed_id=af.FEED_ID, observed_on=af.OBSERVED_ON,
                   catalog_data=CATALOG, providers=af.BROWSER_PROVIDERS), **extra}


def sidecar(path):
    return Path(str(path) + '.provenance.json')


def leftovers(path):
    return sorted(p.name for p in path.parent.iterdir() if p.name != path.name)


def state(path):
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


# -- B1: independent expected-version anchor ------------------------------------------------------

def test_expected_sha1_is_required(zip_path):
    with pytest.raises(admin.AdminError, match='independently supplied archive version is required'):
        admin.plan_archive_record(zip_path, SRC, **kw())
    with pytest.raises(admin.AdminError, match='independently supplied'):
        admin.write_archive_record(zip_path, SRC, **kw())
    assert leftovers(zip_path) == []


@pytest.mark.parametrize('bad', ['', 'abc', 'B48388A26EEFFBADDE634B526DC3CB1ACD8606AB', 'g' * 40, 'a' * 39, 'a' * 41,
                                 ' ' + 'a' * 40])
def test_expected_sha1_format(zip_path, bad):
    before = state(zip_path)
    with pytest.raises(admin.AdminError, match='exactly 40 lowercase'):
        admin.write_archive_record(zip_path, SRC, **kw(expected_sha1=bad))
    assert state(zip_path) == before and leftovers(zip_path) == []


def test_mismatched_expected_sha1_is_refused_and_never_adopted(zip_path):
    before = state(zip_path)
    wrong = 'a' * 40
    plan = admin.plan_archive_record(zip_path, SRC, **kw(expected_sha1=wrong))
    assert any('does not equal the expected archive version' in p for p in plan['problems'])
    with pytest.raises(admin.AdminError, match='does not equal the expected archive version'):
        admin.write_archive_record(zip_path, SRC, **kw(expected_sha1=wrong))
    assert state(zip_path) == before and not sidecar(zip_path).exists() and leftovers(zip_path) == []


def test_expected_record_url_must_be_the_canonical_page(zip_path):
    sha1 = sha1_file(zip_path)
    canonical = f'https://{af.RECORD_HOST}/data/feeds/{af.FEED_ID}/versions/{sha1}'
    for bad in (canonical.replace('https://', 'http://'), canonical + '/', canonical.replace(af.FEED_ID, 'f-other'),
                f'https://{af.RECORD_HOST}/data/feeds/{af.FEED_ID}/versions/{"a" * 40}'):
        plan = admin.plan_archive_record(zip_path, SRC, **kw(expected_sha1=sha1, expected_record_url=bad))
        assert any('not the canonical version page' in p for p in plan['problems']), bad
    plan = admin.plan_archive_record(zip_path, SRC, **kw(expected_sha1=sha1, expected_record_url=canonical))
    assert plan['problems'] == []


def test_matching_anchor_writes_an_honest_record(zip_path):
    sha1 = sha1_file(zip_path)
    before = state(zip_path)
    out = admin.write_archive_record(zip_path, SRC, **kw(expected_sha1=sha1))
    rec = json.loads(sidecar(zip_path).read_text(encoding='utf-8'))
    assert rec == out['record'] and rec['archive']['version_id'] == sha1
    anchor = rec['archive']['version_anchor']
    assert anchor['expected_version_id'] == sha1 and anchor['remote_page_reverified_offline'] is False
    assert anchor['record_url'] == rec['archive']['record_url']
    assert state(zip_path) == before and leftovers(zip_path) == [sidecar(zip_path).name]


def test_real_aspo_download_read_only_regression():
    """Read-only: the user's verified ZIP matches the known Transitland version; nothing is written."""
    if not REAL_DOWNLOAD.is_file():
        pytest.skip('the user-downloaded historical ASPO ZIP is not present')
    folder_before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in REAL_DOWNLOAD.parent.iterdir()}
    mtime = REAL_DOWNLOAD.stat().st_mtime_ns
    plan = admin.plan_archive_record(REAL_DOWNLOAD, 'aspo', provider='transitland', feed_id='f-spqb-aspoolbia',
                                     observed_on='2026-08-28', expected_sha1=REAL_SHA1)
    assert [p for p in plan['problems'] if 'already exists' not in p] == []  # only the existing record blocks it
    wrong = admin.plan_archive_record(REAL_DOWNLOAD, 'aspo', provider='transitland', feed_id='f-spqb-aspoolbia',
                                      observed_on='2026-08-28', expected_sha1='0' * 40)
    assert any('does not equal the expected archive version' in p for p in wrong['problems'])
    # The record already written in Phase 7D1 (no version_anchor) stays valid: compatibility.
    existing = json.loads(sidecar(REAL_DOWNLOAD).read_text(encoding='utf-8')) if sidecar(REAL_DOWNLOAD).is_file() else None
    if existing is not None:
        cat = catalog()
        assert 'version_anchor' not in existing['archive']
        assert archive_problems(existing, source_id='aspo', sha256=existing['sha256'], sha1=REAL_SHA1,
                                size=existing['bytes'], entry=cat['sources']['aspo'], catalog_license=cat.get('license'),
                                providers=load_providers()) == []
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in REAL_DOWNLOAD.parent.iterdir()} == folder_before
    assert REAL_DOWNLOAD.stat().st_mtime_ns == mtime


# -- B2: atomic, no-clobber publication ------------------------------------------------------------

def test_disk_full_while_writing_leaves_nothing(zip_path, monkeypatch):
    def full(path, data):
        Path(path).write_bytes(data[:10])
        raise OSError(28, 'No space left on device (injected)')

    monkeypatch.setattr(admin, '_write_record_bytes', full)
    with pytest.raises(OSError, match='injected'):
        admin.write_archive_record(zip_path, SRC, **kw(expected_sha1=sha1_file(zip_path)))
    assert leftovers(zip_path) == []


def test_serialization_failure_leaves_nothing(zip_path, monkeypatch):
    real = admin.json.dumps
    monkeypatch.setattr(admin.json, 'dumps', lambda obj, **k: (_ for _ in ()).throw(TypeError('injected'))
                        if isinstance(obj, dict) and 'archive' in obj else real(obj, **k))
    with pytest.raises(TypeError, match='injected'):
        admin.write_archive_record(zip_path, SRC, **kw(expected_sha1=sha1_file(zip_path)))
    assert leftovers(zip_path) == []


def test_existing_destination_is_never_replaced(zip_path):
    sidecar(zip_path).write_text('{"someone": "else"}', encoding='utf-8')
    with pytest.raises(admin.AdminError, match='already exists'):
        admin.write_archive_record(zip_path, SRC, **kw(expected_sha1=sha1_file(zip_path)))
    assert sidecar(zip_path).read_text(encoding='utf-8') == '{"someone": "else"}'
    assert leftovers(zip_path) == [sidecar(zip_path).name]


def test_destination_created_concurrently_is_kept(zip_path, monkeypatch):
    real = admin._publish_no_clobber

    def racing(temporary, final):
        Path(final).write_text('{"written": "by another process"}', encoding='utf-8')
        real(temporary, final)

    monkeypatch.setattr(admin, '_publish_no_clobber', racing)
    with pytest.raises(admin.AdminError, match='appeared concurrently; not overwritten'):
        admin.write_archive_record(zip_path, SRC, **kw(expected_sha1=sha1_file(zip_path)))
    assert sidecar(zip_path).read_text(encoding='utf-8') == '{"written": "by another process"}'
    assert leftovers(zip_path) == [sidecar(zip_path).name]


def test_zip_changed_before_publication_publishes_nothing(zip_path, monkeypatch, tmp_path):
    real = admin._write_record_bytes
    other = hf.build(tmp_path / 'z2')['other']

    def write_then_swap(path, data):
        real(path, data)
        shutil.copyfile(other, zip_path)

    monkeypatch.setattr(admin, '_write_record_bytes', write_then_swap)
    with pytest.raises(admin.AdminError, match='nothing was published'):
        admin.write_archive_record(zip_path, SRC, **kw(expected_sha1=sha1_file(zip_path)))
    assert leftovers(zip_path) == []


def test_zip_changed_right_after_publication_removes_only_our_record(zip_path, monkeypatch, tmp_path):
    real = admin._publish_no_clobber
    other = hf.build(tmp_path / 'z2')['other']

    def publish_then_swap(temporary, final):
        real(temporary, final)
        shutil.copyfile(other, zip_path)

    monkeypatch.setattr(admin, '_publish_no_clobber', publish_then_swap)
    with pytest.raises(admin.AdminError, match='removed again'):
        admin.write_archive_record(zip_path, SRC, **kw(expected_sha1=sha1_file(zip_path)))
    assert leftovers(zip_path) == []


def test_filesystem_without_atomic_no_clobber_fails_closed(zip_path, monkeypatch):
    def unsupported(temporary, final):
        raise PermissionError(1, 'hard links not supported (injected)')

    monkeypatch.setattr(admin, '_publish_no_clobber', unsupported)
    with pytest.raises(admin.AdminError, match='cannot publish the record without risking an overwrite'):
        admin.write_archive_record(zip_path, SRC, **kw(expected_sha1=sha1_file(zip_path)))
    assert leftovers(zip_path) == []


def test_success_leaves_no_temporary_files_and_exact_bytes(zip_path):
    out = admin.write_archive_record(zip_path, SRC, **kw(expected_sha1=sha1_file(zip_path)))
    assert leftovers(zip_path) == [sidecar(zip_path).name]
    assert sidecar(zip_path).read_bytes() == (json.dumps(out['record'], indent=2) + '\n').encode('utf-8')
