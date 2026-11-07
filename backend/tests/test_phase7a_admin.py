"""Phase 7A: guarded snapshot administration on synthetic, temporary databases only.

Every database here is created under pytest's tmp_path from the invented feeds in
history_fixtures; the user's real DuckDB is never opened for writing (the only
real-database case is read-only and skips when the backend holds the lock).
"""
from __future__ import annotations

import json
from datetime import date
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pytest
from fastapi.testclient import TestClient

from transport_atlas import admin
from transport_atlas import ingest
from transport_atlas.api import create_app
from transport_atlas.gtfs import FeedError
from transport_atlas.history import SnapshotHistory
from transport_atlas.ingest import import_file
from transport_atlas.storage import connect, rows
from . import history_fixtures as hf

ROOT = Path(__file__).resolve().parents[2]
BACKENDS = ['sqlite', 'duckdb']
FAKE_CATALOG = {'sources': {'synthetic_official': {
    'publisher': 'SYNTHETIC official-style source (test only)', 'landing_url': 'https://www.sardegnamobilita.it/open-data/test',
    'download_hosts': []}}}


class Env:
    def __init__(self, tmp: Path, backend: str):
        self.tmp, self.backend = tmp, backend
        self.db = tmp / ('atlas.sqlite' if backend == 'sqlite' else 'atlas.duckdb')
        self.raw, self.backups = tmp / 'raw', tmp / 'backups'
        self.zips = hf.build(tmp / 'zips')
        con = connect(self.db, backend)
        try:
            # Bootstrap with the legacy importer (activate=True): v1 and the other operator.
            self.v1 = import_file(con, self.zips['v1'], source_id=hf.SOURCE, archive_dir=self.raw,
                                  publisher=hf_publisher())['snapshot_id']
            self.other = import_file(con, self.zips['other'], source_id=hf.OTHER_SOURCE, archive_dir=self.raw,
                                     publisher=hf_publisher())['snapshot_id']
        finally:
            con.close()
        self.target = admin.resolve_database(self.db)

    def state(self):
        con = admin.open_readonly(self.target)
        try:
            return admin.logical_state(con)
        finally:
            con.close()

    def counts(self):
        con = admin.open_readonly(self.target)
        try:
            return {t: admin._one(con, f'SELECT COUNT(*) AS n FROM {t}')['n'] for t in
                    ('feed_sources', 'feed_snapshots', 'selection_members', 'routes', 'stops', 'trips', 'stop_times',
                     'calendars', 'calendar_exceptions', 'frequencies', 'qa_issues')}
        finally:
            con.close()

    def kw(self):
        return dict(backup_root=self.backups, services_record=None)

    def import_v2(self, **extra):
        return admin.apply_import(self.target, self.zips['v2'], hf.SOURCE, synthetic=True, **self.kw(), **extra)

    def backups_made(self):
        return sorted(p.name for p in self.backups.glob('*/backup.json')) if self.backups.exists() else []

    def archives(self):
        return sorted(str(p.relative_to(self.raw)) for p in self.raw.rglob('*') if p.is_file())


def hf_publisher():
    return admin.SYNTHETIC_PUBLISHER


@pytest.fixture(params=BACKENDS)
def env(request, tmp_path):
    if request.param == 'duckdb':
        pytest.importorskip('duckdb')
    return Env(tmp_path, request.param)


@pytest.fixture
def sqlite_env(tmp_path):
    return Env(tmp_path, 'sqlite')


@pytest.fixture
def duck_env(tmp_path):
    pytest.importorskip('duckdb')
    return Env(tmp_path, 'duckdb')


def selection(env):
    return env.state()['selection']


def v2_id(env):
    import hashlib
    digest = admin.sha256_file(env.zips['v2'])
    return hashlib.sha256((hf.SOURCE + ':' + digest).encode()).hexdigest()[:32]


# -- legacy behaviour is unchanged -------------------------------------------------------

def test_legacy_import_still_activates_by_default(tmp_path):
    """scripts/import_local_feeds.py and the Phase 6 fixtures rely on activate=True."""
    con = connect(tmp_path / 'legacy.sqlite', 'sqlite')
    ids = hf.import_all(con, tmp_path / 'zips', tmp_path / 'raw')
    sel = {r['source_id']: r['snapshot_id'] for r in rows(con, 'SELECT * FROM selection_members')}
    assert sel == {hf.SOURCE: ids['v2'], hf.OTHER_SOURCE: ids['other']}
    con.close()


def test_import_file_activate_false_never_touches_selection(tmp_path):
    con = connect(tmp_path / 'x.sqlite', 'sqlite')
    zips = hf.build(tmp_path / 'zips')
    result = import_file(con, zips['v1'], source_id=hf.SOURCE, archive_dir=tmp_path / 'raw', activate=False)
    assert result['activated'] is False and result['archive_created'] is True
    assert rows(con, 'SELECT * FROM selection_members') == []  # a new source stays unselected
    v1 = result['snapshot_id']
    import_file(con, zips['v2'], source_id=hf.SOURCE, archive_dir=tmp_path / 'raw')  # legacy: activates v2
    v2 = rows(con, 'SELECT snapshot_id FROM selection_members')[0]['snapshot_id']
    assert v2 != v1
    con.close()


def test_import_file_refuses_to_overwrite_differing_archived_provenance(tmp_path):
    con = connect(tmp_path / 'x.sqlite', 'sqlite')
    zips = hf.build(tmp_path / 'zips')
    digest = admin.sha256_file(zips['v1'])
    side = Path(str(zips['v1']) + '.provenance.json')
    side.write_text(json.dumps({'source_id': hf.SOURCE, 'sha256': digest, 'retrieved_at': '2026-11-07T00:00:00+00:00'}))
    archived = tmp_path / 'raw' / hf.SOURCE / (digest + '.zip.provenance.json')
    archived.parent.mkdir(parents=True)
    archived.write_text(json.dumps({'source_id': hf.SOURCE, 'sha256': digest, 'retrieved_at': '2020-01-01T00:00:00+00:00'}))
    with pytest.raises(FeedError, match='provenance differs'):
        import_file(con, zips['v1'], source_id=hf.SOURCE, archive_dir=tmp_path / 'raw')
    assert json.loads(archived.read_text())['retrieved_at'].startswith('2020')
    assert rows(con, 'SELECT * FROM feed_snapshots') == []
    con.close()


# -- import preserves the selection; activate / restore -------------------------------------

def test_import_v2_preserves_v1_selection_and_history_lists_both(env):
    before = env.state()
    preview = admin.plan_import(env.target, env.zips['v2'], hf.SOURCE, synthetic=True)
    assert preview['problems'] == [] and preview['action'] == 'import'
    assert preview['selection']['current'] == env.v1 == preview['selection']['after']
    assert env.state() == before and env.backups_made() == []  # preview writes nothing

    out = env.import_v2()
    assert out['applied'] and out['result']['activated'] is False and out['result']['snapshot_id'] == v2_id(env)
    after = env.state()
    assert after['selection'] == before['selection'] == {hf.SOURCE: env.v1, hf.OTHER_SOURCE: env.other}
    assert [s for s in after['snapshots'] if s[0] == v2_id(env)] == [[v2_id(env), hf.SOURCE, preview['sha256'], 'ready']]
    assert admin.verify_backup(out['backup']['metadata_file'])['verified']
    meta = admin.load_backup(out['backup']['metadata_file'])
    assert meta['state'] == before and meta['sha256'] == admin.sha256_file(meta['backup_file'])

    con = admin.open_readonly(env.target)
    try:
        inv = SnapshotHistory(con).inventory(hf.SOURCE)
        assert [(s['snapshot_id'], s['selected']) for s in inv['snapshots']] == [(env.v1, True), (v2_id(env), False)]
        assert inv['comparison_possible']
        diff = SnapshotHistory(con).compare(hf.SOURCE, env.v1, v2_id(env), policy='same_date', service_date=date.fromisoformat(hf.COMMON_DATE))
        assert diff['summary']['trip_templates_on_date']['baseline'] == 8
        assert diff['summary']['trip_templates_on_date']['comparison'] == 9
    finally:
        con.close()


def test_activate_then_restore_round_trip_and_atlas_follows(env):
    env.import_v2()
    v2 = v2_id(env)
    with TestClient(create_app(env.db, env.backend)) as client:
        atlas_v1 = client.get('/api/v1/snapshots').json()
        summary_v1 = client.get(f'/api/v1/summary?date={hf.COMMON_DATE}').json()
    assert {s['snapshot_id'] for s in atlas_v1} == {env.v1, env.other}

    plan = admin.plan_selection(env.target, hf.SOURCE, v2, env.v1)
    assert plan['problems'] == [] and plan['change'] and selection(env)[hf.SOURCE] == env.v1
    out = admin.apply_selection(env.target, hf.SOURCE, v2, env.v1, **env.kw())
    assert out['result'] == {'source_id': hf.SOURCE, 'before': env.v1, 'after': v2, 'changed': True}
    assert selection(env) == {hf.SOURCE: v2, hf.OTHER_SOURCE: env.other}
    with TestClient(create_app(env.db, env.backend)) as client:
        assert {s['snapshot_id'] for s in client.get('/api/v1/snapshots').json()} == {v2, env.other}

    plan = admin.plan_restore(env.target, hf.SOURCE, env.backups)
    assert (plan['current'], plan['to'], plan['problems']) == (v2, env.v1, [])
    restored = admin.apply_restore(env.target, hf.SOURCE, **env.kw())
    assert restored['result']['after'] == env.v1 and selection(env)[hf.SOURCE] == env.v1
    with TestClient(create_app(env.db, env.backend)) as client:
        assert client.get('/api/v1/snapshots').json() == atlas_v1
        assert client.get(f'/api/v1/summary?date={hf.COMMON_DATE}').json() == summary_v1
    phases = [(e['kind'], e['phase']) for e in admin.journal_entries(env.backups, env.target, hf.SOURCE)]
    assert phases == [('activate', 'intent'), ('activate', 'applied'), ('restore', 'intent'), ('restore', 'applied')]
    assert len(env.backups_made()) == 3  # import, activate, restore: one verified backup each


def test_restore_only_when_current_is_the_recorded_after(env):
    env.import_v2()
    v2 = v2_id(env)
    admin.apply_selection(env.target, hf.SOURCE, v2, env.v1, **env.kw())
    # Out-of-band change (another operator): the journal's "after" no longer holds.
    con = admin.open_write(env.target)
    con.execute('DELETE FROM selection_members WHERE source_id=?', (hf.SOURCE,))
    con.commit()
    con.close()
    plan = admin.plan_restore(env.target, hf.SOURCE, env.backups)
    assert any('stale --expected-current' in p for p in plan['problems'])
    made = env.backups_made()
    with pytest.raises(admin.AdminError, match='Refusing restore'):
        admin.apply_restore(env.target, hf.SOURCE, **env.kw())
    assert hf.SOURCE not in selection(env) and env.backups_made() == made
    con = admin.open_write(env.target)
    con.execute('INSERT INTO selection_members VALUES(?,?)', (hf.SOURCE, v2))
    con.commit()
    con.close()
    admin.apply_restore(env.target, hf.SOURCE, **env.kw())
    assert selection(env)[hf.SOURCE] == env.v1


def test_stale_expected_current_fails_with_no_change(env):
    env.import_v2()
    v2 = v2_id(env)
    before = env.state()
    for stale in (admin.NONE, v2, env.other):
        with pytest.raises(admin.AdminError, match='stale'):
            admin.apply_selection(env.target, hf.SOURCE, v2, stale, **env.kw())
    assert env.state() == before and len(env.backups_made()) == 1  # only the import's backup
    # The in-transaction check catches a race the preview could not see.
    con = admin.open_write(env.target)
    try:
        with pytest.raises(admin.AdminError, match='concurrently or --expected-current is stale'):
            admin.change_selection(con, hf.SOURCE, v2, admin.NONE, backend=env.backend)
    finally:
        con.close()
    assert env.state() == before


def test_wrong_source_missing_nonready_and_broken_archive_cannot_be_activated(env):
    env.import_v2()
    v2 = v2_id(env)
    cases = {env.other: 'belongs to source', '0' * 32: 'does not exist'}
    con = admin.open_write(env.target)
    failed = 'f' * 32
    con.execute('INSERT INTO feed_snapshots VALUES(?,?,?,?,?,?,?,?,?)',
                (failed, hf.SOURCE, 'deadbeef' * 8, '2026-11-07T00:00:00+00:00', str(env.raw / 'nope.zip'),
                 'failed', None, None, None))
    con.commit()
    con.close()
    cases[failed] = "import_status 'failed'"
    before = env.state()
    for snap, message in cases.items():
        with pytest.raises(admin.AdminError, match=message):
            admin.apply_selection(env.target, hf.SOURCE, snap, env.v1, **env.kw())
    for bad in ('V2', v2[:8], 'x' * 32):
        with pytest.raises(admin.AdminError, match='full 32-character'):
            admin.plan_selection(env.target, hf.SOURCE, bad, env.v1)
    with pytest.raises(admin.AdminError, match='Unknown source'):
        admin.plan_selection(env.target, 'synthetic_typo', v2, env.v1)
    archive = env.raw / hf.SOURCE / (admin.sha256_file(env.zips['v2']) + '.zip')
    moved = archive.with_name('moved-away')
    archive.replace(moved)
    with pytest.raises(admin.AdminError, match='archive missing'):
        admin.apply_selection(env.target, hf.SOURCE, v2, env.v1, **env.kw())
    moved.write_bytes(moved.read_bytes() + b'tampered')
    moved.replace(archive)
    with pytest.raises(admin.AdminError, match='archive hash mismatch'):
        admin.apply_selection(env.target, hf.SOURCE, v2, env.v1, **env.kw())
    assert env.state() == before and len(env.backups_made()) == 1


def test_duplicate_bytes_import_is_idempotent_and_keeps_selection(env):
    before, counts = env.state(), env.counts()
    out = admin.apply_import(env.target, env.zips['v1'], hf.SOURCE, synthetic=True, **env.kw())
    assert out['action'] == 'existing' and out['applied'] is False and out['snapshot_id'] == env.v1
    assert env.state() == before and env.counts() == counts and env.backups_made() == []
    env.import_v2()
    again = env.import_v2()
    assert again['action'] == 'existing' and again['snapshot_id'] == v2_id(env)
    assert selection(env)[hf.SOURCE] == env.v1


def _crc_corrupt(src: Path, dest: Path) -> Path:
    with ZipFile(src) as z, ZipFile(dest, 'w', compression=ZIP_STORED) as out:
        for name in z.namelist():
            out.writestr(name, z.read(name))
    data = bytearray(dest.read_bytes())
    i = data.index(b'SYNTHETIC Valley Transit')
    data[i] ^= 0x20  # same length, header intact, CRC now wrong
    dest.write_bytes(bytes(data))
    return dest


def test_invalid_zip_crc_and_provenance_failures_leave_everything_unchanged(env, tmp_path):
    before, counts, archives = env.state(), env.counts(), env.archives()
    junk = tmp_path / 'junk.zip'
    junk.write_bytes(b'not a zip at all')
    crc = _crc_corrupt(env.zips['v2'], tmp_path / 'crc.zip')
    invalid = tmp_path / 'invalid.zip'
    with ZipFile(invalid, 'w') as z:
        z.writestr('agency.txt', 'agency_name\nX\n')
    cases = [(junk, {}, 'not a readable ZIP'), (crc, {}, 'CRC failure'), (invalid, {}, 'GTFS validation failed'),
             (env.zips['v2'], {'synthetic': False}, 'no provenance sidecar'),
             (tmp_path / 'missing.zip', {}, 'ZIP not found')]
    for path, extra, message in cases:
        kw = {'synthetic': True, **extra}
        with pytest.raises(admin.AdminError, match=message):
            admin.apply_import(env.target, path, hf.SOURCE, **kw, **env.kw())
    with pytest.raises(admin.AdminError, match='not in this database'):
        admin.apply_import(env.target, env.zips['v2'], 'synthetic_typo', synthetic=True, **env.kw())
    with pytest.raises(admin.AdminError, match='already exists'):
        admin.apply_import(env.target, env.zips['v2'], hf.SOURCE, synthetic=True, new_source=True, **env.kw())
    with pytest.raises(admin.AdminError, match='official catalog source'):
        admin.apply_import(env.target, env.zips['v2'], 'trenitalia_sardegna', synthetic=True, new_source=True, **env.kw())
    assert env.state() == before and env.counts() == counts and env.archives() == archives
    assert env.backups_made() == []


def _provenanced(env, tmp_path, **override):
    zip_path = tmp_path / 'official' / 'synthetic_official.zip'
    zip_path.parent.mkdir(exist_ok=True)
    shutil.copyfile(env.zips['other'], zip_path)
    prov = {'source_id': 'synthetic_official', 'landing_url': FAKE_CATALOG['sources']['synthetic_official']['landing_url'],
            'download_url': 'https://www.sardegnamobilita.it/opendata/test.zip',
            'retrieved_at': '2026-11-07T08:00:00+00:00', 'sha256': admin.sha256_file(zip_path),
            'bytes': zip_path.stat().st_size, **override}
    Path(str(zip_path) + '.provenance.json').write_text(json.dumps(prov), encoding='utf-8')
    return zip_path


@pytest.mark.parametrize('override,message', [
    ({'sha256': '0' * 64}, 'sha256 does not match'),
    ({'source_id': 'arst'}, 'provenance source_id'),
    ({'bytes': 1}, 'byte count'),
    ({'retrieved_at': '2026-11-07T08:00:00'}, 'timezone'),
    ({'download_url': 'http://www.sardegnamobilita.it/x.zip'}, 'download_url rejected'),
    ({'download_url': 'https://evil.example/x.zip'}, 'download_url rejected'),
    ({'landing_url': 'https://www.sardegnamobilita.it/elsewhere'}, 'landing_url differs'),
])
def test_provenance_mismatch_is_refused(sqlite_env, tmp_path, override, message):
    env = sqlite_env
    before = env.state()
    zip_path = _provenanced(env, tmp_path, **override)
    with pytest.raises(admin.AdminError, match=message):
        admin.apply_import(env.target, zip_path, 'synthetic_official', new_source=True,
                           catalog_data=FAKE_CATALOG, **env.kw())
    assert env.state() == before and env.backups_made() == []


def test_valid_provenance_new_source_is_archived_but_not_added_to_the_atlas(env, tmp_path):
    zip_path = _provenanced(env, tmp_path)
    before = env.state()
    out = admin.apply_import(env.target, zip_path, 'synthetic_official', new_source=True,
                             catalog_data=FAKE_CATALOG, **env.kw())
    sid = out['result']['snapshot_id']
    after = env.state()
    assert 'synthetic_official' in after['sources'] and 'synthetic_official' not in after['selection']
    assert after['selection'] == before['selection']
    archived = Path(out['result']['archive_path'])
    assert archived.is_file() and json.loads(archived.with_suffix('.zip.provenance.json').read_text()) == \
        json.loads(Path(str(zip_path) + '.provenance.json').read_text())
    with TestClient(create_app(env.db, env.backend)) as client:
        assert sid not in {s['snapshot_id'] for s in client.get('/api/v1/snapshots').json()}
        src = {s['source_id']: s for s in client.get('/api/v1/history/sources').json()['sources']}['synthetic_official']
        assert src['selected_snapshot_id'] is None and src['ready_snapshots'] == 1
    admin.apply_selection(env.target, 'synthetic_official', sid, admin.NONE, **env.kw())
    assert env.state()['selection']['synthetic_official'] == sid
    admin.apply_restore(env.target, 'synthetic_official', **env.kw())
    assert env.state()['selection'] == before['selection']


def test_importer_failure_rolls_back_and_removes_the_new_archive(env, monkeypatch):
    before, counts, archives = env.state(), env.counts(), env.archives()
    real = ingest.insert_many

    def failing(con, table, statement, values):
        if table == 'stop_times':
            raise RuntimeError('injected failure mid-transaction')
        return real(con, table, statement, values)

    monkeypatch.setattr(ingest, 'insert_many', failing)
    with pytest.raises(RuntimeError, match='injected') as info:
        env.import_v2()
    assert any('Removed unreferenced file' in n for n in getattr(info.value, '__notes__', []))
    assert env.state() == before and env.counts() == counts and env.archives() == archives
    report = admin.status(env.target, extra_archive_dirs=[env.raw])
    assert report['unreferenced_archives'] == [] and report['partial_files'] == []
    assert len(env.backups_made()) == 1  # the backup taken before the failed write remains
    monkeypatch.setattr(ingest, 'insert_many', real)
    assert env.import_v2()['applied']


# -- backups -------------------------------------------------------------------------------

def test_failed_backup_prevents_mutation(env):
    before, counts, archives = env.state(), env.counts(), env.archives()

    def broken_copy(src, dst):
        Path(dst).write_bytes(b'partial')
        raise OSError('disk full (injected)')

    def corrupting_copy(src, dst):
        shutil.copyfile(src, dst)
        with open(dst, 'r+b') as f:
            f.seek(100)
            f.write(b'\x00\xff')

    for copy, message in ((broken_copy, 'disk full'), (corrupting_copy, 'does not match the source')):
        with pytest.raises(admin.AdminError, match='Backup failed; no change was made') as info:
            admin.apply_import(env.target, env.zips['v2'], hf.SOURCE, synthetic=True, copy=copy, **env.kw())
        assert message in str(info.value)
        with pytest.raises(admin.AdminError, match='Backup failed'):
            admin.apply_selection(env.target, hf.OTHER_SOURCE, admin.NONE, env.other, copy=copy, **env.kw())
    assert env.state() == before and env.counts() == counts and env.archives() == archives
    leftovers = list(env.backups.rglob('*')) if env.backups.exists() else []
    assert leftovers == [], leftovers  # no partial backup and no journal entry
    record = env.tmp / 'local-services.json'
    record.write_text('{}')
    with pytest.raises(admin.AdminError, match='recorded as running'):
        admin.apply_selection(env.target, hf.OTHER_SOURCE, admin.NONE, env.other, backup_root=env.backups,
                              services_record=record)
    assert env.state() == before


def test_uncheckpointed_wal_or_journal_blocks_backup(env):
    side = Path(str(env.db) + ('.wal' if env.backend == 'duckdb' else '-journal'))
    side.write_bytes(b'x')
    try:
        with pytest.raises(admin.AdminError, match='Uncheckpointed'):
            admin.guarded(env.target, env.backups, 'x', lambda con: pytest.fail('must not mutate'), services_record=None)
    finally:
        side.unlink()
    assert not env.backups.exists()
    assert env.state()['selection'][hf.OTHER_SOURCE] == env.other


def test_backup_is_hashed_verifiable_and_restorable_to_a_new_path(env, tmp_path):
    pre_sha = admin.sha256_file(env.db)
    before = env.state()
    out = admin.apply_selection(env.target, hf.OTHER_SOURCE, admin.NONE, env.other, **env.kw())
    assert hf.OTHER_SOURCE not in selection(env)
    meta = {**admin.load_backup(out['backup']['metadata_file']), 'metadata_file': out['backup']['metadata_file']}
    assert meta['sha256'] == pre_sha == admin.sha256_file(meta['backup_file'])
    assert meta['source_database'] == str(env.db.resolve()) and meta['backup_file'] != str(env.db)
    assert Path(meta['backup_file']).parent.parent == env.backups.resolve()
    dest = tmp_path / 'restored' / env.db.name
    preview = admin.restore_backup(meta['metadata_file'], dest)
    assert preview['applied'] is False and not dest.exists()
    admin.restore_backup(meta['metadata_file'], dest, apply=True)
    assert admin.sha256_file(dest) == pre_sha
    con = admin.open_readonly(admin.resolve_database(dest))
    try:
        assert admin.logical_state(con) == before
    finally:
        con.close()
    with pytest.raises(admin.AdminError, match='never overwrites'):
        admin.restore_backup(meta['metadata_file'], dest, apply=True)
    with pytest.raises(admin.AdminError, match='Destination exists'):
        admin.restore_backup(meta['metadata_file'], env.db, apply=True)
    with open(meta['backup_file'], 'r+b') as f:  # tampering is detected
        f.seek(200)
        f.write(b'\x13\x37')
    with pytest.raises(admin.AdminError, match='hash mismatch'):
        admin.verify_backup(meta['metadata_file'])
    # Backups never overwrite each other: two backups in quick succession get distinct folders.
    a = admin.create_backup(env.target, env.backups, label='a')
    b = admin.create_backup(env.target, env.backups, label='b')
    assert a['backup_file'] != b['backup_file']


# -- locks, crashes, partial files ---------------------------------------------------------------

HOLD = '''
import sys, duckdb
con = duckdb.connect(sys.argv[1])
print('locked', flush=True)
sys.stdin.readline()  # hold the lock until the test closes stdin
con.close()
'''


def test_locked_duckdb_fails_safely_without_backup(duck_env):
    env = duck_env
    before_sha = admin.sha256_file(env.db)
    # Closing stdin (not kill) ends the holder: on Windows the venv launcher's child
    # would otherwise keep the lock after the launcher is killed.
    holder = subprocess.Popen([sys.executable, '-c', HOLD, str(env.db)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == 'locked'
        for call in (lambda: admin.apply_selection(env.target, hf.OTHER_SOURCE, admin.NONE, env.other, **env.kw()),
                     lambda: env.import_v2(),
                     lambda: admin.status(env.target),
                     lambda: admin.create_backup(env.target, env.backups, label='x')):
            with pytest.raises(admin.AdminError, match='locked by another process'):
                call()
        assert not env.backups.exists() or list(env.backups.rglob('*')) == []
    finally:
        holder.stdin.close()
        holder.wait(timeout=60)
    assert admin.sha256_file(env.db) == before_sha
    assert selection(env)[hf.OTHER_SOURCE] == env.other


def test_locked_sqlite_fails_safely_without_backup(sqlite_env):
    import sqlite3
    env = sqlite_env
    other = sqlite3.connect(env.db)
    other.execute('BEGIN EXCLUSIVE')
    try:
        with pytest.raises(admin.AdminError, match='locked by another process'):
            admin.apply_selection(env.target, hf.OTHER_SOURCE, admin.NONE, env.other, **env.kw())
    finally:
        other.rollback()
        other.close()
    assert not env.backups.exists() or list(env.backups.rglob('*')) == []
    assert selection(env)[hf.OTHER_SOURCE] == env.other


CRASH = '''
import os, sys
sys.path[:0] = [sys.argv[1]]
from transport_atlas import admin, ingest
real = ingest.insert_many
def crash(con, table, statement, values):
    if table == 'qa_issues':
        os._exit(17)  # hard crash after the archive exists, before COMMIT
    return real(con, table, statement, values)
ingest.insert_many = crash
target = admin.resolve_database(sys.argv[2])
admin.apply_import(target, sys.argv[3], 'synthetic_valley', synthetic=True, backup_root=sys.argv[4], services_record=None)
'''


def test_hard_crash_mid_import_leaves_db_unchanged_and_a_reported_reusable_archive(duck_env):
    env = duck_env
    before, counts = env.state(), env.counts()
    proc = subprocess.run([sys.executable, '-c', CRASH, str(ROOT / 'backend'), str(env.db), str(env.zips['v2']),
                           str(env.backups)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 17, proc.stderr
    wal = Path(str(env.db) + '.wal')
    if wal.exists():
        # DuckDB may leave a WAL after a crash: the admin tool refuses to back up until
        # the owner reopens (replays/checkpoints) it.
        with pytest.raises(admin.AdminError, match='Uncheckpointed'):
            env.import_v2()
        import duckdb
        duckdb.connect(str(env.db)).close()
    assert env.state() == before and env.counts() == counts
    archive = env.raw / hf.SOURCE / (admin.sha256_file(env.zips['v2']) + '.zip')
    report = admin.status(env.target)
    assert report['unreferenced_archives'] == [str(archive)]
    out = env.import_v2()
    assert out['applied'] and out['result']['archive_created'] is False
    assert admin.status(env.target)['unreferenced_archives'] == []
    assert selection(env)[hf.SOURCE] == env.v1


def test_partial_files_are_reported_not_used(env):
    stray = env.raw / hf.SOURCE / ('a' * 64 + '.zip.1234.part')
    stray.write_bytes(b'half')
    report = admin.status(env.target)
    assert report['partial_files'] == [str(stray)]
    env.import_v2()
    assert stray.read_bytes() == b'half'  # never touched by the importer


# -- CLI --------------------------------------------------------------------------------------------

def cli(*args):
    sys.path.insert(0, str(ROOT / 'scripts'))
    import manage_snapshots
    return manage_snapshots.main([str(a) for a in args])


def test_cli_preview_by_default_and_explicit_apply(env, monkeypatch, capsys):
    monkeypatch.setattr(admin, 'SERVICES_RECORD', env.tmp / 'no-services.json')
    db, b = env.db, env.backups
    assert cli('status', '--db', db, '--json') == 0
    status = json.loads(capsys.readouterr().out)
    assert status['database']['path'] == str(db.resolve()) and not status['database']['real_atlas_database']
    before = env.state()
    assert cli('import', '--db', db, '--source', hf.SOURCE, '--zip', env.zips['v2'], '--synthetic', '--backup-dir', b) == 0
    assert 'Preview only' in capsys.readouterr().out and env.state() == before and env.backups_made() == []
    assert cli('import', '--db', db, '--source', hf.SOURCE, '--zip', env.zips['v2'], '--synthetic',
               '--backup-dir', b, '--apply') == 0
    capsys.readouterr()
    v2 = v2_id(env)
    assert env.state()['selection'] == before['selection']
    assert cli('activate', '--db', db, '--source', hf.SOURCE, '--snapshot', v2, '--expected-current', 'none',
               '--backup-dir', b) == 2
    assert 'REFUSED' in capsys.readouterr().out
    assert cli('activate', '--db', db, '--source', hf.SOURCE, '--snapshot', v2, '--expected-current', env.v1,
               '--backup-dir', b, '--apply') == 0
    assert selection(env)[hf.SOURCE] == v2
    assert cli('restore', '--db', db, '--source', hf.SOURCE, '--backup-dir', b, '--apply') == 0
    assert selection(env)[hf.SOURCE] == env.v1
    capsys.readouterr()
    assert cli('status', '--db', env.tmp / 'typo.sqlite') == 2
    assert 'Refusing to create' in capsys.readouterr().err and not (env.tmp / 'typo.sqlite').exists()


def test_cli_refuses_real_database_mutation_without_flag(sqlite_env, monkeypatch, capsys):
    env = sqlite_env
    monkeypatch.setattr(admin, 'REAL_DB', env.db)  # stand-in: the real DB is never targeted by tests
    monkeypatch.setattr(admin, 'SERVICES_RECORD', env.tmp / 'no-services.json')
    before = env.state()
    assert cli('activate', '--db', env.db, '--source', hf.OTHER_SOURCE, '--snapshot', 'none',
               '--expected-current', env.other, '--backup-dir', env.backups, '--apply') == 2
    assert '--real-database' in capsys.readouterr().err
    assert env.state() == before and env.backups_made() == []


def test_real_database_preflight_is_read_only():
    path = ROOT / 'data' / 'transport_atlas.duckdb'
    if not path.is_file():
        pytest.skip('Local real DuckDB not present')
    pytest.importorskip('duckdb')
    try:
        sha = admin.sha256_file(path)  # Windows denies reads while the backend holds the write lock
    except PermissionError:
        pytest.skip('Real DuckDB locked by the running backend; stop services to run this case')
    try:
        report = admin.status(admin.resolve_database(path), verify_archives=True)
    except admin.AdminError as exc:
        pytest.skip(f'Real DuckDB locked (services running?): {exc}')
    assert report['database']['real_atlas_database']
    assert len(report['sources']) == 4
    for src in report['sources']:
        [snap] = src['snapshots']
        assert snap['selected'] and snap['import_status'] == 'ready'
        assert snap['archive']['present'] and snap['archive']['sha256_verified'] and snap['archive']['provenance_sidecar']
    assert admin.sha256_file(path) == sha
