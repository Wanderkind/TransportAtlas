"""Phase 7C: honest provenance for GTFS copies obtained from a third-party archive.

A snapshot's provenance lives in the immutable, content-addressed sidecar next to
its archived ZIP (``<raw>/<source>/<sha256>.zip.provenance.json``). The import path
refuses to overwrite a differing sidecar, so the evidence of one ZIP can never be
replaced by another import. No database table or column is added: the real
database is never migrated (see docs/PHASE7C_ARCHIVE_PROVENANCE.md).

Evidence classes (``classify``):

* ``publisher_direct``      this computer downloaded the bytes from the publisher's
                            official URL (the Phase 3+ official downloader's sidecar);
* ``third_party_archived``  the bytes were obtained from a named archive (e.g.
                            Transitland). The publisher is recorded as *context* and
                            as the archive's reported origin, never as our download
                            origin;
* ``synthetic``             invented fixture without real provenance;
* ``unknown_legacy``        no (or an unusable) provenance record: shown as unknown,
                            never filled in with invented facts.

An archive sidecar deliberately has no top-level ``download_url`` or ``landing_url``,
so older readers can never mistake the archive for an official download.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from .gtfs import FeedError
from .official import ALLOWED_OFFICIAL_HOSTS, safe_url

ARCHIVE_SCHEMA = 'transport-atlas/third-party-archive-provenance/1'
PUBLISHER_DIRECT = 'publisher_direct'
THIRD_PARTY = 'third_party_archived'
SYNTHETIC = 'synthetic'
LEGACY = 'unknown_legacy'
KIND_LABELS = {
    PUBLISHER_DIRECT: 'Downloaded from the publisher by this computer',
    THIRD_PARTY: 'Archived copy from a third-party archive',
    SYNTHETIC: 'Synthetic fixture (not real history)',
    LEGACY: 'Provenance unknown (no usable record)',
}
DEFAULT_PROVIDERS = Path(__file__).resolve().parents[2] / 'configs' / 'archive_providers.json'
SHA1_RE = re.compile(r'[0-9a-f]{40}')
# Key or value fragments that suggest a credential ended up in a provenance record.
SECRET_RE = re.compile(r'(api[_-]?key|apikey|token|secret|password|passwd|cookie|authorization|bearer|session)', re.I)


def load_providers(path: str | Path = DEFAULT_PROVIDERS) -> dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha1_file(path: str | Path) -> str:
    h = hashlib.sha1()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def is_archive_record(prov: dict | None) -> bool:
    return isinstance(prov, dict) and (prov.get('schema') == ARCHIVE_SCHEMA or prov.get('provenance_kind') == THIRD_PARTY)


def _aware(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.utcoffset() is not None else None


def _https(url, hosts: set[str], what: str, problems: list[str]) -> None:
    try:
        safe_url(url or '', allowed_hosts=hosts)
    except FeedError as exc:
        problems.append(f'{what} rejected: {exc}')
        return
    parts = urlsplit(url)
    if parts.query or parts.fragment:
        problems.append(f'{what} must not carry a query or fragment (credentials are never recorded)')


def _secrets(value, path='') -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_RE.search(str(key)):
                found.append(f'{path}{key}')
            found += _secrets(item, f'{path}{key}.')
    elif isinstance(value, list):
        for n, item in enumerate(value):
            found += _secrets(item, f'{path}{n}.')
    elif isinstance(value, str) and ('?' in value or '=' in value) and SECRET_RE.search(value):
        found.append(path.rstrip('.') or '<value>')
    return found


def archive_problems(prov: dict, *, source_id: str, sha256: str, sha1: str, size: int,
                     entry: dict | None, catalog_license: dict | None, providers: dict,
                     now: datetime | None = None) -> list[str]:
    """Every reason this third-party archive record is not acceptable for these bytes."""
    problems: list[str] = []
    if not isinstance(prov, dict):
        return ['archive provenance must be a JSON object']
    if prov.get('schema') != ARCHIVE_SCHEMA:
        problems.append(f'archive provenance schema must be {ARCHIVE_SCHEMA!r}')
    if prov.get('provenance_kind') != THIRD_PARTY:
        problems.append(f'provenance_kind must be {THIRD_PARTY!r}')
    for forbidden in ('download_url', 'landing_url'):
        if forbidden in prov:
            problems.append(f'top-level {forbidden!r} is not allowed in an archive record '
                            '(it would read as an official download); use publisher.* and archive.*')
    if prov.get('source_id') != source_id:
        problems.append(f'provenance source_id {prov.get("source_id")!r} != {source_id!r}')
    if prov.get('sha256') != sha256:
        problems.append('provenance sha256 does not match the ZIP')
    if prov.get('bytes') != size:
        problems.append('provenance byte count missing or does not match the ZIP')
    retrieved = _aware(prov.get('retrieved_at'))
    now = now or datetime.now(timezone.utc)
    if retrieved is None:
        problems.append('retrieved_at (local acquisition time) missing, invalid or without timezone')
    elif retrieved > now + timedelta(minutes=5):
        problems.append('retrieved_at lies in the future')
    secrets = _secrets(prov)
    if secrets:
        problems.append('provenance contains credential-like fields or values: ' + ', '.join(secrets[:5]))

    if entry is None:
        problems.append(f'{source_id!r} is not in the official catalog; the original publisher cannot be checked')
    publisher = prov.get('publisher') if isinstance(prov.get('publisher'), dict) else None
    if publisher is None:
        problems.append('publisher context (publisher.name, publisher.landing_url) is required')
    elif entry is not None:
        if publisher.get('name') != entry.get('publisher'):
            problems.append('publisher.name differs from the official catalog')
        if publisher.get('landing_url') != entry.get('landing_url'):
            problems.append('publisher.landing_url differs from the official catalog')
        reported = publisher.get('reported_origin_url')
        if reported is not None:
            _https(reported, ALLOWED_OFFICIAL_HOSTS | set(entry.get('download_hosts', [])),
                   'publisher.reported_origin_url', problems)

    archive = prov.get('archive') if isinstance(prov.get('archive'), dict) else None
    if archive is None:
        problems.append('archive record (archive.provider, record_url, version_id, download_url) is required')
        return problems
    name = archive.get('provider')
    provider = (providers.get('providers') or {}).get(name)
    if provider is None:
        problems.append(f'archive provider {name!r} is not a known provider')
        return problems
    if archive.get('provider_name') != provider.get('name'):
        problems.append(f'archive.provider_name must be {provider.get("name")!r} (the registered provider name)')
    if not provider.get('import_enabled'):
        problems.append(f'archive provider {name!r} is not enabled for imports: {provider.get("disabled_reason", "")}'.rstrip(': '))
    record_hosts = set(provider.get('record_hosts', []))
    download_hosts = set(provider.get('download_hosts', []))
    if download_hosts & (ALLOWED_OFFICIAL_HOSTS | set((entry or {}).get('download_hosts', []))):
        problems.append(f'provider {name!r} lists an official publisher host as an archive host; refusing')
    version = archive.get('version_id')
    if provider.get('version_id') == 'sha1':
        if not isinstance(version, str) or not SHA1_RE.fullmatch(version):
            problems.append('archive.version_id must be the archive\'s 40-hex SHA-1 of the ZIP')
        elif version != sha1:
            problems.append('archive.version_id (archive SHA-1) does not match the SHA-1 of these bytes')
    _https(archive.get('record_url'), record_hosts, 'archive.record_url', problems)
    if isinstance(version, str) and version and version not in str(archive.get('record_url') or ''):
        problems.append('archive.record_url does not refer to archive.version_id')
    if not download_hosts:
        problems.append(f'provider {name!r} has no verified download hosts')
    else:
        _https(archive.get('download_url'), download_hosts, 'archive.download_url', problems)
        final = archive.get('final_download_url')
        if final is not None:
            _https(final, download_hosts, 'archive.final_download_url', problems)
    observed = archive.get('observed_at')
    if observed is not None:
        observed_at = _aware(observed)
        if observed_at is None:
            problems.append('archive.observed_at must be a timezone-aware ISO time (or null if unknown)')
        elif retrieved is not None and observed_at > retrieved:
            problems.append('archive.observed_at is later than the local retrieval time')
        if not archive.get('observed_at_evidence_url'):
            problems.append('archive.observed_at needs observed_at_evidence_url (where it was read)')
        else:
            _https(archive.get('observed_at_evidence_url'), record_hosts, 'archive.observed_at_evidence_url', problems)

    lic = prov.get('license') if isinstance(prov.get('license'), dict) else None
    if lic is None:
        problems.append('license (name, attribution, evidence_url) is required')
    else:
        if not lic.get('name'):
            problems.append('license.name is required')
        if not (lic.get('attribution') or '').strip():
            problems.append('license.attribution text is required')
        if not str(lic.get('evidence_url') or '').startswith('https://'):
            problems.append('license.evidence_url must be an https URL')
        expected = ((entry or {}).get('license') or catalog_license or {}).get('name')
        if expected and lic.get('name') != expected:
            problems.append(f'license.name {lic.get("name")!r} differs from the catalog licence {expected!r}')
    return problems


# -- read side (History, route provenance, status) ------------------------------------

@lru_cache(maxsize=256)
def _read(path: str, size: int, mtime_ns: int):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return ValueError


def read_sidecar(raw_zip_path: str | Path):
    side = Path(str(raw_zip_path)[:-4] + '.zip.provenance.json') if str(raw_zip_path).endswith('.zip') \
        else Path(str(raw_zip_path) + '.provenance.json')
    if not side.is_file():
        return None
    st = side.stat()
    return _read(str(side), st.st_size, st.st_mtime_ns)


@lru_cache(maxsize=1)
def _known_providers() -> dict:
    try:
        return load_providers().get('providers', {})
    except (OSError, ValueError):
        return {}


def _provider_name(key) -> str | None:
    return (_known_providers().get(key) or {}).get('name') or key


def classify(snap: dict, *, synthetic: bool = False, publisher: str | None = None,
             landing_url: str | None = None) -> dict:
    """Describe a snapshot's evidence without inventing anything the record lacks."""
    raw = read_sidecar(snap['raw_zip_path'])
    out = {'kind': LEGACY, 'label': KIND_LABELS[LEGACY], 'synthetic': synthetic,
           'publisher': {'name': publisher, 'landing_url': landing_url, 'reported_origin_url': None},
           'download_origin': None, 'archive': None, 'license': None,
           'local_retrieved_at': snap['retrieved_at'], 'observed_at': None, 'observation_basis': 'unknown',
           'problem': None}
    if raw is ValueError:
        out['problem'] = 'The provenance record is unreadable.'
    elif raw is not None and (raw.get('source_id') != snap['source_id'] or raw.get('sha256') != snap['content_sha256']):
        out['problem'] = 'The provenance record does not match this snapshot (source or SHA-256 differs).'
    elif raw is not None and is_archive_record(raw):
        archive = raw.get('archive') or {}
        pub = raw.get('publisher') or {}
        out.update(kind=THIRD_PARTY, label=KIND_LABELS[THIRD_PARTY],
                   publisher={'name': pub.get('name') or publisher, 'landing_url': pub.get('landing_url') or landing_url,
                              'reported_origin_url': pub.get('reported_origin_url')},
                   download_origin=archive.get('download_url'),
                   archive={**{k: archive.get(k) for k in ('provider', 'record_url', 'version_id', 'observed_at',
                                                           'observed_at_evidence_url', 'download_url')},
                            'provider_name': archive.get('provider_name') or _provider_name(archive.get('provider'))},
                   license=raw.get('license'), observed_at=archive.get('observed_at'),
                   observation_basis='archive_record' if archive.get('observed_at') else 'unknown')
    elif raw is not None and raw.get('download_url'):
        out.update(kind=PUBLISHER_DIRECT, label=KIND_LABELS[PUBLISHER_DIRECT],
                   publisher={'name': publisher, 'landing_url': raw.get('landing_url') or landing_url,
                              'reported_origin_url': None},
                   download_origin=raw.get('download_url'), observed_at=snap['retrieved_at'],
                   observation_basis='local_retrieval')
    if out['kind'] == LEGACY and synthetic and out['problem'] is None:
        out.update(kind=SYNTHETIC, label=KIND_LABELS[SYNTHETIC])
    return out
