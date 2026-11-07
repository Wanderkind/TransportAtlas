"""Phase 7D1: browser-acquired archive provenance, on synthetic temporary data only.

A copy the user downloaded in a browser is anchored on the provider's public version
page and the SHA-1 of the bytes; its delivery address is recorded as unknown. The
provider, feeds and publisher here are invented; the real ZIP and database are never
opened.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from transport_atlas import admin
from transport_atlas.api import create_app
from transport_atlas.archive_provenance import (BROWSER, THIRD_PARTY, archive_problems, browser_record,
                                                load_providers, sha1_file)
from transport_atlas.history import SnapshotHistory
from transport_atlas.official import ALLOWED_OFFICIAL_HOSTS, catalog
from transport_atlas.route_details import route_evidence
from . import archive_fixtures as af
from . import history_fixtures as hf
from .test_phase7c_archive_provenance import CATALOG, PUB, SRC, ArchEnv

ROOT = Path(__file__).resolve().parents[2]


def kw(zip_path=None, **extra):
    """Record-helper arguments; the expected SHA-1 is the one an operator would read from the
    provider's version page (here: of the fixture ZIP, supplied independently of the helper)."""
    if zip_path is not None:
        extra.setdefault('expected_sha1', sha1_file(zip_path))
    return {**dict(provider=af.BROWSER_PROVIDER, feed_id=af.FEED_ID, observed_on=af.OBSERVED_ON,
                   catalog_data=CATALOG, providers=af.BROWSER_PROVIDERS), **extra}


class BrowserEnv(ArchEnv):
    """As Phase 7C's environment, but the older copy came from a browser download."""

    def __init__(self, tmp, backend):
        super().__init__(tmp, backend)
        Path(str(self.older) + '.provenance.json').unlink()  # drop the 7C record
        self.written = admin.write_archive_record(self.older, SRC, **kw(self.older))
        self.record = self.written['record']

    def kw(self, **extra):
        return {**super().kw(**extra), 'providers': af.BROWSER_PROVIDERS}


@pytest.fixture(params=['sqlite', 'duckdb'])
def env(request, tmp_path):
    if request.param == 'duckdb':
        pytest.importorskip('duckdb')
    return BrowserEnv(tmp_path, request.param)


@pytest.fixture
def sqlite_env(tmp_path):
    return BrowserEnv(tmp_path, 'sqlite')


def _zip(tmp_path, key='v1'):
    path = tmp_path / 'downloads' / 'archived.zip'
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(hf.build(tmp_path / 'zips')[key], path)
    return path


# -- the record helper ----------------------------------------------------------------------

def test_helper_previews_then_writes_once_without_touching_the_zip(tmp_path):
    path = _zip(tmp_path)
    digest, mtime = admin.sha256_file(path), path.stat().st_mtime_ns
    plan = admin.plan_archive_record(path, SRC, **kw(path))
    side = Path(plan['sidecar'])
    assert plan['problems'] == [] and not side.exists()
    rec, arc = plan['record'], plan['record']['archive']
    assert arc['acquisition_method'] == BROWSER and arc['delivery'] == 'unknown'
    assert 'download_url' not in arc and 'final_download_url' not in arc and 'download_url' not in rec
    assert arc['version_id'] == sha1_file(path)
    assert arc['record_url'] == f'https://{af.RECORD_HOST}/data/feeds/{af.FEED_ID}/versions/{arc["version_id"]}'
    assert arc['observed_on'] == af.OBSERVED_ON and 'observed_at' not in arc
    assert rec['sha256'] == digest and rec['bytes'] == path.stat().st_size
    assert rec['retrieved_at_basis'] == 'file_modification_time'
    assert datetime.fromisoformat(rec['retrieved_at']) == datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    assert rec['publisher'] == {'name': PUB, 'landing_url': CATALOG['sources'][SRC]['landing_url']}
    assert rec['license']['name'] == 'CC-BY-4.0' and 'not endorsed by the publisher' in rec['license']['attribution']
    out = admin.write_archive_record(path, SRC, **kw(path))
    assert out['written'] and json.loads(side.read_text(encoding='utf-8')) == out['record']
    with pytest.raises(admin.AdminError, match='already exists'):
        admin.write_archive_record(path, SRC, **kw(path))
    assert admin.sha256_file(path) == digest and path.stat().st_mtime_ns == mtime
    stated = admin.plan_archive_record(path, SRC,
                                       **kw(path, retrieved_at='2026-11-07T11:00:00+00:00'))
    assert stated['record']['retrieved_at_basis'] == 'stated_by_user'


def test_helper_refuses_problems_and_paths_inside_the_archive_store(tmp_path, monkeypatch):
    path = _zip(tmp_path)
    with pytest.raises(admin.AdminError, match='Refusing to write'):
        admin.write_archive_record(path, SRC, **kw(path, observed_on='2099-01-01'))
    assert not Path(str(path) + '.provenance.json').exists()
    disabled = json.loads(json.dumps(af.BROWSER_PROVIDERS))
    disabled['providers'][af.BROWSER_PROVIDER]['browser_acquisition']['enabled'] = False
    assert any('does not accept browser-acquired' in p for p in
               admin.plan_archive_record(path, SRC, **{**kw(path), 'providers': disabled})['problems'])
    with pytest.raises(admin.AdminError, match='not in the official catalog'):
        admin.plan_archive_record(path, 'not_in_catalog', **kw(path))
    monkeypatch.setattr(admin, 'ROOT', tmp_path)
    inside = tmp_path / 'data' / 'raw' / SRC / 'x.zip'
    inside.parent.mkdir(parents=True)
    shutil.copyfile(path, inside)
    with pytest.raises(admin.AdminError, match='inside data/raw'):
        admin.plan_archive_record(inside, SRC, **kw(inside))


# -- import and read side ---------------------------------------------------------------------

def test_browser_copy_imports_attributed_with_delivery_unknown(env):
    before, source_before = env.state(), env.source_row()
    out = env.apply()
    assert out['applied'] and out['provenance_kind'] == THIRD_PARTY
    assert env.state()['selection'] == before['selection'] == {SRC: env.v2}
    assert env.source_row() == source_before
    old = out['result']['snapshot_id']
    archived = Path(out['result']['archive_path']).with_suffix('.zip.provenance.json')
    assert json.loads(archived.read_text(encoding='utf-8')) == env.record
    con = env.con()
    try:
        inv = SnapshotHistory(con).inventory(SRC, date.fromisoformat(hf.COMMON_DATE), sort='observed')
        evidence = route_evidence(con, old, 'R2')['provenance']
    finally:
        con.close()
    item = inv['snapshots'][0]
    assert item['snapshot_id'] == old  # observed 2026-08-28, before the current copy
    prov = item['provenance']
    assert prov['kind'] == THIRD_PARTY and prov['download_origin'] is None
    assert prov['archive']['acquisition_method'] == BROWSER and prov['archive']['delivery_known'] is False
    assert prov['archive']['feed_id'] == af.FEED_ID and prov['archive']['observed_on'] == af.OBSERVED_ON
    assert item['observed_at'] == af.OBSERVED_ON and item['observation_basis'] == 'archive_record_date'
    assert item['retrieved_at'] == env.record['retrieved_at']
    assert evidence['download_url'] is None
    with TestClient(create_app(env.db, env.backend)) as client:
        cmp = client.get(f'/api/v1/history/{SRC}/compare', params={
            'baseline': old, 'comparison': env.v2, 'policy': 'same_date', 'date': hf.COMMON_DATE}).json()
    notice = next(n['text'] for n in cmp['notices'] if n['code'] == 'baseline_third_party_archived')
    assert "downloaded through the user’s browser; delivery address not recorded" in notice
    assert '2026-08-28 (date only)' in notice and 'not downloaded from the publisher' in notice
    assert cmp['baseline']['provenance']['archive']['acquisition_method'] == BROWSER


def _bad(env, mutate):
    rec = json.loads(json.dumps(env.record))
    mutate(rec)
    side = Path(str(env.older) + '.provenance.json')
    side.write_text(json.dumps(rec), encoding='utf-8')


def _set(path, value):
    def apply(rec):
        target = rec
        *parents, last = path.split('.')
        for key in parents:
            target = target[key]
        if value is KeyError:
            target.pop(last, None)
        else:
            target[last] = value
    return apply


@pytest.mark.parametrize('mutate,message', [
    (_set('archive.download_url', f'https://{af.RECORD_HOST}/x.zip'), 'archive.download_url must be absent'),
    (_set('archive.final_download_url', f'https://{af.RECORD_HOST}/x.zip'), 'archive.final_download_url must be absent'),
    (_set('archive.delivery', KeyError), 'delivery must be "unknown"'),
    (_set('archive.delivery', f'https://{af.RECORD_HOST}'), 'delivery must be "unknown"'),
    (_set('archive.record_url', f'https://{af.RECORD_HOST}/feeds/{af.FEED_ID}/versions/X'), 'exactly the public version page'),
    (_set('archive.feed_id', 'f-someone-else'), 'exactly the public version page'),
    (_set('archive.feed_id', 'ASPO'), 'archive.feed_id must be'),
    (_set('archive.version_id', 'a' * 40), 'does not match the SHA-1'),
    (_set('archive.observed_on', '2026/08/28'), 'must be a date YYYY-MM-DD'),
    (_set('archive.observed_on', '2026-08-28T00:00:00'), 'must be a date YYYY-MM-DD'),
    (_set('archive.observed_on', '2099-01-01'), 'later than the local retrieval date'),
    (_set('archive.observed_at', '2026-08-28T06:00:00+00:00'), 'not both'),
    (_set('retrieved_at_basis', KeyError), 'retrieved_at_basis must be'),
    (_set('retrieved_at_basis', 'guessed'), 'retrieved_at_basis must be'),
    (_set('archive.account_id', '12345'), "must not identify the user's account"),
    (_set('archive.note', 'downloaded by someone@example.test'), "must not identify the user's account"),
    (_set('archive.acquisition_method', 'scraped'), 'acquisition_method must be'),
    (_set('archive.session', 'abc'), 'credential-like'),
    (_set('publisher.name', 'ASPO S.p.a'), 'publisher.name differs'),
])
def test_invalid_browser_records_are_refused(sqlite_env, mutate, message):
    env = sqlite_env
    _bad(env, mutate)
    before, files = env.state(), env.files()
    with pytest.raises(admin.AdminError, match='Refusing import') as info:
        env.apply()
    assert message in str(info.value), str(info.value)
    assert env.state() == before and env.files() == files and env.backups_made() == []


def test_modes_stay_separate(sqlite_env):
    env = sqlite_env
    # A browser record is never accepted on the official path ...
    with pytest.raises(admin.AdminError, match='pass --archive-provenance'):
        admin.apply_import(env.target, env.older, SRC, **{k: v for k, v in env.kw().items() if k != 'providers'})
    # ... nor by a provider that has no browser mode (the 7C invented provider).
    with pytest.raises(admin.AdminError, match='not a known provider'):
        admin.apply_import(env.target, env.older, SRC, archive=True, **{**env.kw(), 'providers': af.PROVIDERS})
    # A provider-download (7C) record may not use the browser-only date field.
    rec = af.record(env.older, SRC, PUB, sha256=admin.sha256_file(env.older), retrieved_at=env.record['retrieved_at'],
                    observed_at=None)
    rec['archive']['observed_on'] = '2026-08-28'
    problems = archive_problems(rec, source_id=SRC, sha256=rec['sha256'], sha1=sha1_file(env.older), size=rec['bytes'],
                                entry=CATALOG['sources'][SRC], catalog_license=CATALOG['license'], providers=af.PROVIDERS)
    assert any('only for browser-acquired' in p for p in problems)
    assert env.backups_made() == []


def test_zip_replaced_during_backup_imports_only_verified_bytes(sqlite_env):
    env = sqlite_env
    verified = admin.sha256_file(env.older)

    def swapping_copy(src, dst):
        shutil.copyfile(env.zips['other'], env.older)
        shutil.copyfile(src, dst)

    out = env.apply(copy=swapping_copy)
    assert admin.sha256_file(env.older) != verified
    assert out['result']['sha256'] == verified and admin.sha256_file(out['result']['archive_path']) == verified


def test_record_forged_during_backup_is_not_used(sqlite_env):
    env = sqlite_env
    side = Path(str(env.older) + '.provenance.json')

    def forging_copy(src, dst):
        forged = json.loads(json.dumps(env.record))
        forged['archive']['download_url'] = 'https://www.aspo.it/wp-content/uploads/open_data.zip'
        side.write_text(json.dumps(forged), encoding='utf-8')
        shutil.copyfile(src, dst)

    out = env.apply(copy=forging_copy)
    archived = Path(out['result']['archive_path']).with_suffix('.zip.provenance.json')
    assert json.loads(archived.read_text(encoding='utf-8')) == env.record


# -- the shipped configuration ------------------------------------------------------------------

def test_shipped_transitland_entry_accepts_only_browser_copies(tmp_path):
    providers = load_providers()
    entry = providers['providers']['transitland']
    assert entry['import_enabled'] is False and entry['download_hosts'] == []
    browser = entry['browser_acquisition']
    assert browser['enabled'] is True
    assert browser['record_url_pattern'] == 'https://www.transit.land/data/feeds/{feed_id}/versions/{sha1}'
    assert ALLOWED_OFFICIAL_HOSTS == {'sardegnamobilita.it', 'www.sardegnamobilita.it'}
    # In memory only: a record for INVENTED bytes validates against the real catalog and
    # registry, showing the path is ready without touching the real ZIP or database.
    path = _zip(tmp_path)
    cat = catalog()
    rec = browser_record(path, source_id='aspo', entry=cat['sources']['aspo'], catalog_license=cat.get('license'),
                         provider_key='transitland', provider=entry, feed_id='f-spqb-aspoolbia', observed_on='2026-08-28')
    assert rec['archive']['record_url'] == f'https://www.transit.land/data/feeds/f-spqb-aspoolbia/versions/{sha1_file(path)}'
    assert archive_problems(rec, source_id='aspo', sha256=rec['sha256'], sha1=sha1_file(path), size=rec['bytes'],
                            entry=cat['sources']['aspo'], catalog_license=cat.get('license'), providers=providers) == []
    assert 'ASPO Olbia' in rec['license']['attribution'] and 'Transitland Feed Archive' in rec['license']['attribution']
    # An API-mode Transitland record is still refused.
    api = json.loads(json.dumps(rec))
    api['archive'].update(acquisition_method='provider_download', download_url='https://transit.land/x.zip')
    del api['archive']['delivery']
    problems = archive_problems(api, source_id='aspo', sha256=rec['sha256'], sha1=sha1_file(path), size=rec['bytes'],
                                entry=cat['sources']['aspo'], catalog_license=cat.get('license'), providers=providers)
    assert any('not enabled for imports' in p for p in problems)


def test_cli_archive_record_previews_by_default(tmp_path):
    path = _zip(tmp_path)
    cmd = [sys.executable, str(ROOT / 'scripts' / 'manage_snapshots.py'), 'archive-record', '--zip', str(path),
           '--source', 'not_in_catalog', '--provider', 'transitland', '--feed-id', 'f-x', '--acquisition',
           'authenticated_browser', '--observed-on', '2026-08-28', '--expected-sha1', sha1_file(path)]
    done = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 2 and 'not in the official catalog' in done.stderr
    done = subprocess.run(cmd + ['--write'], capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 2 and not Path(str(path) + '.provenance.json').exists()
    help_text = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'manage_snapshots.py'), '--help'],
                               capture_output=True, text=True, cwd=ROOT).stdout
    assert 'archive-record' in help_text
