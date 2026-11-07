"""Phase 7C: honest third-party archive provenance, on synthetic temporary databases only.

The "official-style" source, its publisher and the archive provider are invented
(see archive_fixtures). No real feed, archive, account or download is involved, and
the user's real DuckDB is never opened here.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from transport_atlas import admin, ingest
from transport_atlas.api import create_app
from transport_atlas.archive_provenance import (ARCHIVE_SCHEMA, LEGACY, PUBLISHER_DIRECT, SYNTHETIC, THIRD_PARTY,
                                                archive_problems, classify, load_providers, sha1_file)
from transport_atlas.gtfs import FeedError
from transport_atlas.history import SnapshotHistory
from transport_atlas.ingest import import_file
from transport_atlas.official import ALLOWED_OFFICIAL_HOSTS
from transport_atlas.route_details import route_evidence
from transport_atlas.storage import SCHEMA, connect, rows
from . import archive_fixtures as af
from . import history_fixtures as hf

ROOT = Path(__file__).resolve().parents[2]
SRC = 'synthetic_official'
PUB = 'SYNTHETIC official-style source (test only)'
CATALOG = af.catalog(SRC, PUB)
OFFICIAL_URL = 'https://www.sardegnamobilita.it/opendata/test-current.zip'
OBSERVED = '2026-08-28T06:00:00+00:00'


def _now(hours=-1):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


class ArchEnv:
    """The current (newer) feed v2 is a publisher-direct snapshot and selected; the older
    v1 is offered as a copy from the invented archive, obtained *after* v2 (like ASPO)."""

    def __init__(self, tmp: Path, backend: str):
        self.tmp, self.backend = tmp, backend
        self.db = tmp / ('atlas.sqlite' if backend == 'sqlite' else 'atlas.duckdb')
        self.raw, self.backups = tmp / 'raw', tmp / 'backups'
        self.zips = hf.build(tmp / 'zips')
        current = tmp / 'official' / 'current.zip'
        current.parent.mkdir()
        shutil.copyfile(self.zips['v2'], current)
        self.official_record = {'source_id': SRC, 'sha256': admin.sha256_file(current), 'retrieved_at': _now(-48),
                                'download_url': OFFICIAL_URL, 'landing_url': CATALOG['sources'][SRC]['landing_url'],
                                'bytes': current.stat().st_size}
        Path(str(current) + '.provenance.json').write_text(json.dumps(self.official_record), encoding='utf-8')
        con = connect(self.db, backend)
        try:
            self.v2 = import_file(con, current, source_id=SRC, archive_dir=self.raw, publisher=PUB,
                                  landing_url=CATALOG['sources'][SRC]['landing_url'])['snapshot_id']
        finally:
            con.close()
        self.target = admin.resolve_database(self.db)
        self.older = self.incoming('v1')
        self.record = af.record(self.older, SRC, PUB, sha256=admin.sha256_file(self.older),
                                retrieved_at=_now(), observed_at=OBSERVED)
        af.write(self.older, self.record)

    def incoming(self, key, name='older.zip'):
        path = self.tmp / 'incoming' / name
        path.parent.mkdir(exist_ok=True)
        shutil.copyfile(self.zips[key], path)
        return path

    def kw(self, **extra):
        return dict(backup_root=self.backups, services_record=None, catalog_data=CATALOG,
                    providers=af.PROVIDERS, **extra)

    def apply(self, path=None, **extra):
        return admin.apply_import(self.target, path or self.older, SRC, archive=True, **self.kw(**extra))

    def con(self):
        return admin.open_readonly(self.target)

    def state(self):
        con = self.con()
        try:
            return admin.logical_state(con)
        finally:
            con.close()

    def source_row(self):
        con = self.con()
        try:
            return admin._one(con, 'SELECT * FROM feed_sources WHERE source_id=?', (SRC,))
        finally:
            con.close()

    def sidecars(self):
        return {p.name: p.read_bytes() for p in self.raw.rglob('*.provenance.json')}

    def files(self):
        return sorted(str(p.relative_to(self.raw)) for p in self.raw.rglob('*') if p.is_file())

    def backups_made(self):
        return sorted(p.name for p in self.backups.glob('*/backup.json')) if self.backups.exists() else []


@pytest.fixture(params=['sqlite', 'duckdb'])
def env(request, tmp_path):
    if request.param == 'duckdb':
        pytest.importorskip('duckdb')
    return ArchEnv(tmp_path, request.param)


@pytest.fixture
def sqlite_env(tmp_path):
    return ArchEnv(tmp_path, 'sqlite')


# -- the existing official path keeps its semantics -----------------------------------------

def test_publisher_direct_import_keeps_its_behaviour(sqlite_env, tmp_path):
    env = sqlite_env
    path = env.incoming('other', 'official.zip')
    record = {'source_id': SRC, 'sha256': admin.sha256_file(path), 'retrieved_at': _now(),
              'download_url': 'https://www.sardegnamobilita.it/opendata/test-new.zip',
              'landing_url': CATALOG['sources'][SRC]['landing_url'], 'bytes': path.stat().st_size}
    Path(str(path) + '.provenance.json').write_text(json.dumps(record), encoding='utf-8')
    before = env.state()
    out = admin.apply_import(env.target, path, SRC, **env.kw())
    assert out['applied'] and out['provenance_kind'] == PUBLISHER_DIRECT
    assert env.state()['selection'] == before['selection'] == {SRC: env.v2}
    # Unchanged legacy semantics: a publisher-direct import records the latest official URL.
    assert env.source_row()['download_url'] == record['download_url']
    con = env.con()
    try:
        snap = admin._one(con, 'SELECT * FROM feed_snapshots WHERE snapshot_id=?', (out['result']['snapshot_id'],))
    finally:
        con.close()
    prov = classify(snap, publisher=PUB)
    assert prov['kind'] == PUBLISHER_DIRECT and prov['download_origin'] == record['download_url']
    assert prov['observed_at'] == record['retrieved_at'] and prov['observation_basis'] == 'local_retrieval'


# -- an archived copy is attributed to its archive -------------------------------------------

def test_archived_copy_is_attributed_to_the_archive(env):
    before, source_before, sidecars_before = env.state(), env.source_row(), env.sidecars()
    out = env.apply()
    assert out['applied'] and out['provenance_kind'] == THIRD_PARTY and out['sha1'] == sha1_file(env.older)
    after = env.state()
    assert after['selection'] == before['selection'] and after['selection'][SRC] == env.v2  # activate=False
    assert env.source_row() == source_before  # official download URL, publisher, landing, licence untouched
    archived = Path(out['result']['archive_path']).with_suffix('.zip.provenance.json')
    assert json.loads(archived.read_text(encoding='utf-8')) == env.record
    for name, data in sidecars_before.items():  # the current snapshot's evidence is untouched
        assert env.sidecars()[name] == data
    con = env.con()
    try:
        history = SnapshotHistory(con)
        inv = history.inventory(SRC, date.fromisoformat(hf.COMMON_DATE), sort='observed')
        old = next(s for s in inv['snapshots'] if s['snapshot_id'] == out['result']['snapshot_id'])
        evidence = route_evidence(con, old['snapshot_id'], 'R2')['provenance']
    finally:
        con.close()
    prov = old['provenance']
    assert prov['kind'] == THIRD_PARTY and prov['synthetic'] is True
    assert prov['archive']['provider_name'] == af.PROVIDERS['providers'][af.PROVIDER]['name']
    assert prov['archive']['version_id'] == sha1_file(env.older)
    assert prov['download_origin'].startswith(f'https://{af.DOWNLOAD_HOST}/')
    assert prov['publisher'] == env.record['publisher']
    assert prov['license']['name'] == 'CC-BY-4.0' and 'SYNTHETIC Example Archive' in prov['license']['attribution']
    # Archive observation (August) is never shown as our retrieval time.
    assert old['observed_at'] == OBSERVED and old['observation_basis'] == 'archive_record'
    assert old['retrieved_at'] == env.record['retrieved_at'] != OBSERVED
    # The inspector never presents the archive URL as the official download.
    assert evidence['provenance_kind'] == THIRD_PARTY and evidence['download_url'] is None


def test_two_snapshots_keep_independent_immutable_provenance(sqlite_env):
    env = sqlite_env
    sidecars_before, source_before = env.sidecars(), env.source_row()
    env.apply()
    sidecars = env.sidecars()
    assert len(sidecars) == 2
    for name, data in sidecars_before.items():
        assert sidecars[name] == data
    assert env.source_row() == source_before
    con = env.con()
    try:
        kinds = {s['snapshot_id']: s['provenance']['kind'] for s in SnapshotHistory(con).inventory(SRC)['snapshots']}
    finally:
        con.close()
    assert kinds[env.v2] == PUBLISHER_DIRECT and sorted(kinds.values()) == [PUBLISHER_DIRECT, THIRD_PARTY]


# -- refusals ----------------------------------------------------------------------------------

def _bad(env, mutate):
    rec = json.loads(json.dumps(env.record))
    mutate(rec)
    af.write(env.older, rec)


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
    (_set('schema', KeyError), 'schema must be'),
    (_set('provenance_kind', 'publisher_direct'), 'provenance_kind must be'),
    (_set('source_id', 'someone_else'), 'source_id'),
    (_set('sha256', '0' * 64), 'sha256 does not match'),
    (_set('bytes', 1), 'byte count'),
    (_set('retrieved_at', KeyError), 'retrieved_at'),
    (_set('retrieved_at', '2026-11-07T08:00:00'), 'without timezone'),
    (_set('retrieved_at', '2099-01-01T00:00:00+00:00'), 'in the future'),
    (_set('archive.version_id', 'f' * 40), 'does not match the SHA-1'),
    (_set('archive.version_id', 'not-a-sha1'), "40-hex SHA-1"),
    (_set('archive.download_url', f'http://{af.DOWNLOAD_HOST}/x.zip'), 'archive.download_url rejected'),
    (_set('archive.download_url', 'https://mirror.example.test/x.zip'), 'archive.download_url rejected'),
    (_set('archive.download_url', OFFICIAL_URL), 'archive.download_url rejected'),
    (_set('archive.download_url', f'https://{af.DOWNLOAD_HOST}/x.zip?apikey=SECRET'), 'query'),
    (_set('archive.final_download_url', 'https://cdn.example.test/x.zip'), 'final_download_url rejected'),
    (_set('archive.record_url', 'https://www.sardegnamobilita.it/open-data/test'), 'record_url rejected'),
    (_set('archive.record_url', f'https://{af.RECORD_HOST}/feeds/x/versions/other'), 'does not refer'),
    (_set('archive.observed_at', '2026-08-28T06:00:00'), 'observed_at must be'),
    (_set('archive.observed_at', '2099-01-01T00:00:00+00:00'), 'later than the local retrieval'),
    (_set('archive.observed_at_evidence_url', KeyError), 'observed_at_evidence_url'),
    (_set('archive.provider', 'transitland_lookalike'), 'not a known provider'),
    (_set('archive.provider_name', 'Transitland Feed Archive'), 'provider_name must be'),
    (_set('archive', KeyError), 'archive record'),
    (_set('download_url', OFFICIAL_URL), "top-level 'download_url'"),
    (_set('landing_url', 'https://www.sardegnamobilita.it/open-data/test'), "top-level 'landing_url'"),
    (_set('publisher.name', 'Someone Else'), 'publisher.name differs'),
    (_set('publisher.landing_url', 'https://www.sardegnamobilita.it/open-data/other'), 'publisher.landing_url differs'),
    (_set('publisher.reported_origin_url', 'http://www.sardegnamobilita.it/opendata/test.zip'), 'reported_origin_url rejected'),
    (_set('publisher', KeyError), 'publisher context'),
    (_set('license.name', 'CC-BY-SA-4.0'), 'differs from the catalog licence'),
    (_set('license.attribution', ' '), 'attribution text'),
    (_set('license.evidence_url', 'http://example.test'), 'evidence_url'),
    (_set('archive.api_key', 'abc'), 'credential-like'),
])
def test_invalid_archive_records_are_refused(sqlite_env, mutate, message):
    env = sqlite_env
    _bad(env, mutate)
    before, files = env.state(), env.files()
    with pytest.raises(admin.AdminError, match='Refusing import') as info:
        env.apply()
    assert message in str(info.value), str(info.value)
    assert env.state() == before and env.files() == files and env.backups_made() == []


def test_disabled_or_unknown_provider_and_catalog_gaps_are_refused(sqlite_env):
    env = sqlite_env
    disabled = json.loads(json.dumps(af.PROVIDERS))
    disabled['providers'][af.PROVIDER]['import_enabled'] = False
    with pytest.raises(admin.AdminError, match='not enabled for imports'):
        admin.apply_import(env.target, env.older, SRC, archive=True, **{**env.kw(), 'providers': disabled})
    overlap = json.loads(json.dumps(af.PROVIDERS))
    overlap['providers'][af.PROVIDER]['download_hosts'].append('www.sardegnamobilita.it')
    with pytest.raises(admin.AdminError, match='lists an official publisher host'):
        admin.apply_import(env.target, env.older, SRC, archive=True, **{**env.kw(), 'providers': overlap})
    with pytest.raises(admin.AdminError, match='not in the official catalog'):
        admin.apply_import(env.target, env.older, SRC, archive=True, **{**env.kw(), 'catalog_data': {'sources': {}}})
    assert env.backups_made() == []


def test_shipped_transitland_entry_is_disabled_and_separate_from_official_hosts():
    providers = load_providers()
    entry = providers['providers']['transitland']
    assert entry['import_enabled'] is False and entry['download_hosts'] == []
    for name, provider in providers['providers'].items():
        assert not set(provider.get('download_hosts', [])) & ALLOWED_OFFICIAL_HOSTS
        assert not set(provider.get('record_hosts', [])) & ALLOWED_OFFICIAL_HOSTS
    sha1 = 'b48388a26eeffbadde634b526dc3cb1acd8606ab'  # the ASPO version named in Phase 7B (metadata only)
    rec = {'schema': ARCHIVE_SCHEMA, 'provenance_kind': THIRD_PARTY, 'source_id': 'aspo', 'sha256': 'x', 'bytes': 1,
           'retrieved_at': _now(), 'publisher': {}, 'license': {},
           'archive': {'provider': 'transitland', 'provider_name': entry['name'], 'version_id': sha1,
                       'record_url': f'https://www.transit.land/data/feeds/f-spqb-aspoolbia/versions/{sha1}',
                       'download_url': 'https://www.transit.land/x.zip'}}
    problems = archive_problems(rec, source_id='aspo', sha256='y', sha1='z', size=2, entry=None,
                                catalog_license=None, providers=providers)
    assert any('not enabled' in p for p in problems) and any('no verified download hosts' in p for p in problems)
    # The official downloader's allowlist is unchanged by Phase 7C.
    assert ALLOWED_OFFICIAL_HOSTS == {'sardegnamobilita.it', 'www.sardegnamobilita.it'}


def test_modes_never_cross(sqlite_env, tmp_path):
    env = sqlite_env
    # An archive record without --archive-provenance is refused (never passes as official) ...
    with pytest.raises(admin.AdminError, match='pass --archive-provenance'):
        admin.apply_import(env.target, env.older, SRC, **{k: v for k, v in env.kw().items() if k != 'providers'})
    # ... and an official-style record with --archive-provenance is refused too.
    official = env.incoming('other', 'official.zip')
    Path(str(official) + '.provenance.json').write_text(json.dumps(
        {**env.official_record, 'sha256': admin.sha256_file(official), 'bytes': official.stat().st_size}), encoding='utf-8')
    with pytest.raises(admin.AdminError, match='schema must be'):
        env.apply(official)
    with pytest.raises(admin.AdminError, match='contradict'):
        env.apply(synthetic=True)
    # The legacy bootstrap importer refuses archive records unless explicitly told, and never activates them.
    con = connect(tmp_path / 'boot.sqlite', 'sqlite')
    try:
        with pytest.raises(FeedError, match='third-party archived copy'):
            import_file(con, env.older, source_id=SRC, archive_dir=tmp_path / 'raw2')
        with pytest.raises(FeedError, match='never activated'):
            import_file(con, env.older, source_id=SRC, archive_dir=tmp_path / 'raw2', archive_provenance=True)
        with pytest.raises(FeedError, match='requires a third-party archive'):
            import_file(con, official, source_id=SRC, archive_dir=tmp_path / 'raw2', archive_provenance=True,
                        activate=False)
        assert rows(con, 'SELECT * FROM feed_snapshots') == []
    finally:
        con.close()
    assert not (tmp_path / 'raw2').exists()


# -- idempotence, races and failure injection ----------------------------------------------------

def test_duplicate_bytes_are_idempotent_and_never_rewrite_evidence(sqlite_env):
    env = sqlite_env
    first = env.apply()
    snapshot_sidecars, state = env.sidecars(), env.state()
    again = env.apply()
    assert again['applied'] is False and 'nothing to do' in again['note'] and len(env.backups_made()) == 1
    # Same bytes, different evidence: refused; the stored evidence stays.
    _bad(env, _set('retrieved_at', _now(-2)))
    with pytest.raises(admin.AdminError, match='differs from the sidecar'):
        env.apply()
    # Archive evidence for bytes already held as the publisher-direct current snapshot: refused.
    current = env.incoming('v2', 'current-again.zip')
    af.write(current, af.record(current, SRC, PUB, sha256=admin.sha256_file(current), retrieved_at=_now(),
                                observed_at=OBSERVED))
    with pytest.raises(admin.AdminError, match='differs from the sidecar'):
        env.apply(current)
    assert env.sidecars() == snapshot_sidecars and env.state() == state
    assert any(s[0] == first['result']['snapshot_id'] for s in state['snapshots'])


def test_zip_replaced_during_backup_imports_only_verified_bytes(env):
    verified = admin.sha256_file(env.older)

    def swapping_copy(src, dst):
        shutil.copyfile(env.zips['other'], env.older)
        shutil.copyfile(src, dst)

    out = env.apply(copy=swapping_copy)
    assert admin.sha256_file(env.older) != verified  # the swap happened
    assert out['result']['sha256'] == verified and admin.sha256_file(out['result']['archive_path']) == verified


def test_provenance_forged_during_backup_is_not_used(sqlite_env):
    env = sqlite_env
    side = Path(str(env.older) + '.provenance.json')

    def forging_copy(src, dst):
        forged = {**env.record, 'download_url': OFFICIAL_URL}  # disguise as an official download
        side.write_text(json.dumps(forged), encoding='utf-8')
        shutil.copyfile(src, dst)

    source_before = env.source_row()
    out = env.apply(copy=forging_copy)
    assert 'download_url' in json.loads(side.read_text(encoding='utf-8'))
    archived = Path(out['result']['archive_path']).with_suffix('.zip.provenance.json')
    assert json.loads(archived.read_text(encoding='utf-8')) == env.record
    assert env.source_row() == source_before


def test_input_changing_while_staged_is_refused(sqlite_env, monkeypatch):
    env = sqlite_env
    real_copy = shutil.copyfile

    def racing_copy(src, dst, *a, **k):
        result = real_copy(src, dst, *a, **k)
        if Path(src) == env.older.resolve():
            real_copy(env.zips['other'], env.older)
        return result

    monkeypatch.setattr(admin.shutil, 'copyfile', racing_copy)
    before = env.state()
    with pytest.raises(admin.AdminError, match='changed while being staged'):
        env.apply(copy=real_copy)
    assert env.state() == before and env.backups_made() == []


def test_sidecar_write_failure_leaves_nothing_behind(env, monkeypatch):
    before, files = env.state(), env.files()

    def fail(path, text):
        Path(path).write_text(text[:7], encoding='utf-8')
        raise OSError('disk full while writing provenance (injected)')

    monkeypatch.setattr(ingest, '_write_text', fail)
    with pytest.raises(OSError, match='injected') as info:
        env.apply()
    assert any('Removed unreferenced file' in n for n in getattr(info.value, '__notes__', []))
    assert env.state() == before and env.files() == files


def test_importer_that_touches_the_official_url_is_caught(sqlite_env):
    env = sqlite_env

    def misbehaving(con, *a, **k):
        result = import_file(con, *a, **k)
        con.execute('UPDATE feed_sources SET download_url=? WHERE source_id=?', ('https://evil.example.test/x.zip', SRC))
        con.commit()
        return result

    with pytest.raises(admin.AdminError, match='INVARIANT VIOLATION.*official download URL'):
        env.apply(importer=misbehaving)
    assert len(env.backups_made()) == 1  # the guard took a verified backup first
    assert env.state()['selection'][SRC] == env.v2


# -- read side: legacy, API, sorting, comparison --------------------------------------------------

def test_legacy_and_inconsistent_records_are_shown_as_unknown(tmp_path):
    con = connect(tmp_path / 'x.sqlite', 'sqlite')
    zips = hf.build(tmp_path / 'zips')
    try:
        sid = import_file(con, zips['v1'], source_id='legacy_src', archive_dir=tmp_path / 'raw',
                          publisher='Legacy Operator (invented)')['snapshot_id']
        snap = rows(con, 'SELECT * FROM feed_snapshots WHERE snapshot_id=?', (sid,))[0]
        prov = classify(snap, publisher='Legacy Operator (invented)')
        assert prov['kind'] == LEGACY and prov['observed_at'] is None and prov['observation_basis'] == 'unknown'
        assert prov['download_origin'] is None and prov['archive'] is None and prov['license'] is None
        side = Path(snap['raw_zip_path']).with_suffix('.zip.provenance.json')
        side.write_text(json.dumps({'source_id': 'legacy_src', 'sha256': '0' * 64, 'download_url': OFFICIAL_URL}),
                        encoding='utf-8')
        prov = classify(snap)
        assert prov['kind'] == LEGACY and 'does not match' in prov['problem'] and prov['download_origin'] is None
        side.write_text('{not json', encoding='utf-8')
        assert 'unreadable' in classify(snap)['problem']
        # An invented fixture without any record is "synthetic", not "publisher".
        side.unlink()
        assert classify(snap, synthetic=True)['kind'] == SYNTHETIC
    finally:
        con.close()


def test_api_labels_sorts_and_compares_on_one_service_date(env):
    out = env.apply()
    old = out['result']['snapshot_id']
    with TestClient(create_app(env.db, env.backend)) as client:
        by_retrieval = client.get(f'/api/v1/history/{SRC}/snapshots', params={'date': hf.COMMON_DATE}).json()
        by_observation = client.get(f'/api/v1/history/{SRC}/snapshots',
                                    params={'date': hf.COMMON_DATE, 'sort': 'observed'}).json()
        assert client.get(f'/api/v1/history/{SRC}/snapshots', params={'sort': 'published'}).status_code == 422
        compare = client.get(f'/api/v1/history/{SRC}/compare', params={
            'baseline': old, 'comparison': env.v2, 'policy': 'same_date', 'date': hf.COMMON_DATE}).json()
        selected = client.get('/api/v1/snapshots').json()
    # The archived copy was obtained last, but observed first; the two orders are labelled.
    assert [s['snapshot_id'] for s in by_retrieval['snapshots']] == [env.v2, old] and by_retrieval['sort'] == 'retrieved'
    assert [s['snapshot_id'] for s in by_observation['snapshots']] == [old, env.v2]
    assert 'local retrieval' in by_retrieval['sort_note'] and 'not a publication date' in by_observation['sort_note']
    kinds = {s['snapshot_id']: s['provenance']['kind'] for s in by_observation['snapshots']}
    assert kinds == {old: THIRD_PARTY, env.v2: PUBLISHER_DIRECT}
    assert all(s['date_in_coverage'] for s in by_observation['snapshots'])
    assert compare['policy'] == 'same_date' and compare['synthetic'] is True
    assert compare['baseline']['provenance']['kind'] == THIRD_PARTY and compare['baseline']['observed_at'] == OBSERVED
    assert compare['comparison']['provenance']['kind'] == PUBLISHER_DIRECT
    notice = next(n for n in compare['notices'] if n['code'] == 'baseline_third_party_archived')
    assert 'SYNTHETIC Example Archive' in notice['text'] and 'not downloaded from the publisher' in notice['text']
    assert compare['summary']['trip_templates_on_date']['status'] == 'changed'
    assert compare['summary']['trip_templates_on_date']['attributable_to_feed_version'] is True
    assert {s['snapshot_id'] for s in selected if s['source_id'] == SRC} == {env.v2}


def test_status_reports_provenance_kinds(sqlite_env):
    env = sqlite_env
    old = env.apply()['result']['snapshot_id']
    report = admin.status(env.target, source_id=SRC)
    kinds = {s['snapshot_id']: s['provenance_kind'] for s in report['sources'][0]['snapshots']}
    assert kinds == {env.v2: PUBLISHER_DIRECT, old: THIRD_PARTY}


def test_cli_exposes_archive_mode_and_previews_by_default(sqlite_env):
    env = sqlite_env
    help_text = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'manage_snapshots.py'), 'import', '--help'],
                               capture_output=True, text=True, cwd=ROOT).stdout
    assert '--archive-provenance' in help_text
    before = env.state()
    # The CLI uses the shipped registry (Transitland disabled) and the real catalog, so the
    # invented source is refused, and nothing is written in preview mode either way.
    done = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'manage_snapshots.py'), 'import', '--db', str(env.db),
                           '--source', SRC, '--zip', str(env.older), '--archive-provenance', '--json'],
                          capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 2 and 'not a known provider' in done.stdout + done.stderr
    assert env.state() == before and env.backups_made() == []


# -- no migration -------------------------------------------------------------------------------

EXPECTED_TABLES = {
    'feed_sources': ['source_id', 'publisher', 'landing_url', 'download_url', 'license_name'],
    'feed_snapshots': ['snapshot_id', 'source_id', 'content_sha256', 'retrieved_at', 'raw_zip_path', 'import_status',
                       'timezone', 'feed_start', 'feed_end'],
    'selection_members': ['source_id', 'snapshot_id'],
}


def test_schema_is_unchanged_so_startup_cannot_migrate_the_real_database(tmp_path):
    """Phase 7C keeps provenance in per-snapshot sidecars. The app's startup DDL is
    CREATE ... IF NOT EXISTS of the same schema the real database already has, so it
    adds nothing; this test fails if a future change adds a table or column."""
    connect(tmp_path / 's.sqlite', 'sqlite').close()
    raw = sqlite3.connect(tmp_path / 's.sqlite')
    try:
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {t: [r[1] for r in raw.execute(f'PRAGMA table_info({t})')] for t in EXPECTED_TABLES}
    finally:
        raw.close()
    assert columns == EXPECTED_TABLES
    assert tables == {'feed_sources', 'feed_snapshots', 'selection_members', 'agencies', 'routes', 'stops', 'trips',
                      'stop_times', 'calendars', 'calendar_exceptions', 'shapes', 'frequencies', 'qa_issues'}
    assert hashlib.sha256(SCHEMA.encode()).hexdigest() == SCHEMA_DIGEST, 'storage.SCHEMA changed: review migration safety'


# storage.SCHEMA at b9894482 / c5cae7c (identical): the schema the real database already has.
SCHEMA_DIGEST = '76ad985cfb55ab8d8d6b0d5f005d8f427bfc9aebb650adb92bf8ae53fdda84ce'
