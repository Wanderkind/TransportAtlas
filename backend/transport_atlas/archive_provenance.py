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

Phase 7D1 adds ``archive.acquisition_method``:

* ``provider_download`` (default; Phase 7C): the bytes were fetched from a verified
  provider download host, recorded in ``archive.download_url``;
* ``authenticated_browser``: the user downloaded the copy in a web browser with their
  own account. The delivery address is not known, so the record must say
  ``delivery: "unknown"`` and carry no download URL. It is anchored instead on the
  provider's public version page (exact URL pattern) and on the archive SHA-1, which
  must equal the SHA-1 of the bytes. A date-only ``observed_on`` may replace
  ``observed_at`` when only the date is public. No account details are allowed.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
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
FEED_ID_RE = re.compile(r'f-[0-9a-z~_.-]{1,120}')
PROVIDER_DOWNLOAD = 'provider_download'
BROWSER = 'authenticated_browser'
RETRIEVED_BASES = ('file_modification_time', 'stated_by_user')
# Keys that would identify the user's account; forbidden in browser-acquired records.
ACCOUNT_RE = re.compile(r'(account|user(name)?$|^user|e-?mail|login|customer|plan_id|subscription)', re.I)
EMAIL_RE = re.compile(r'[^@\s]+@[^@\s]+\.[a-z]{2,}', re.I)
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
    method = archive.get('acquisition_method', PROVIDER_DOWNLOAD)
    if method not in (PROVIDER_DOWNLOAD, BROWSER):
        problems.append(f'archive.acquisition_method must be {PROVIDER_DOWNLOAD!r} or {BROWSER!r}')
        return problems
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
    if method == BROWSER:
        problems += _browser_problems(prov, archive, provider, name, sha1, retrieved)
    else:
        if not provider.get('import_enabled'):
            problems.append(f'archive provider {name!r} is not enabled for imports: {provider.get("disabled_reason", "")}'.rstrip(': '))
        if not download_hosts:
            problems.append(f'provider {name!r} has no verified download hosts')
        else:
            _https(archive.get('download_url'), download_hosts, 'archive.download_url', problems)
            final = archive.get('final_download_url')
            if final is not None:
                _https(final, download_hosts, 'archive.final_download_url', problems)
        if 'observed_on' in archive:
            problems.append('archive.observed_on is only for browser-acquired records; use observed_at')
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


def _browser_problems(prov, archive, provider, name, sha1, retrieved) -> list[str]:
    """Rules for a copy the user downloaded in a browser: anchored on the public version
    page and the SHA-1; the delivery address is recorded as unknown, never guessed."""
    problems = []
    browser = provider.get('browser_acquisition') or {}
    if not browser.get('enabled'):
        problems.append(f'archive provider {name!r} does not accept browser-acquired copies')
    for key in ('download_url', 'final_download_url'):
        if archive.get(key) is not None:
            problems.append(f'archive.{key} must be absent for a browser-acquired copy '
                            '(the delivery address is not known; never guess it)')
    if archive.get('delivery') != 'unknown':
        problems.append('archive.delivery must be "unknown" for a browser-acquired copy')
    feed = archive.get('feed_id')
    if not isinstance(feed, str) or not FEED_ID_RE.fullmatch(feed):
        problems.append('archive.feed_id must be the provider\'s feed id (e.g. f-spqb-aspoolbia)')
    pattern = browser.get('record_url_pattern')
    if not pattern:
        problems.append(f'provider {name!r} has no record_url_pattern for browser-acquired copies')
    elif isinstance(feed, str) and archive.get('record_url') != pattern.format(feed_id=feed, sha1=sha1):
        problems.append('archive.record_url must be exactly the public version page for this feed and these bytes '
                        f'({pattern})')
    if prov.get('retrieved_at_basis') not in RETRIEVED_BASES:
        problems.append(f'retrieved_at_basis must be one of {RETRIEVED_BASES}')
    if archive.get('observed_on') is not None:
        if archive.get('observed_at') is not None:
            problems.append('give either archive.observed_on (date only) or archive.observed_at, not both')
        try:
            day = date.fromisoformat(str(archive['observed_on']))
            if len(str(archive['observed_on'])) != 10:
                raise ValueError
            if retrieved is not None and day > retrieved.astimezone(timezone.utc).date():
                problems.append('archive.observed_on is later than the local retrieval date')
        except ValueError:
            problems.append('archive.observed_on must be a date YYYY-MM-DD')
    accounts = _account_fields(prov)
    if accounts:
        problems.append('browser-acquired records must not identify the user\'s account: ' + ', '.join(accounts[:5]))
    return problems


def _account_fields(value, path='') -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if ACCOUNT_RE.search(str(key)):
                found.append(f'{path}{key}')
            found += _account_fields(item, f'{path}{key}.')
    elif isinstance(value, list):
        for n, item in enumerate(value):
            found += _account_fields(item, f'{path}{n}.')
    elif isinstance(value, str) and EMAIL_RE.search(value) and not path.startswith('license.'):
        found.append(path.rstrip('.') or '<value>')
    return found


def browser_record(zip_path: str | Path, *, source_id: str, entry: dict, catalog_license: dict | None,
                   provider_key: str, provider: dict, feed_id: str, observed_on: str | None,
                   retrieved_at: str | None = None) -> dict:
    """Build (not write) a browser-acquired archive record from computed facts only."""
    path = Path(zip_path)
    data = path.read_bytes()
    sha1 = hashlib.sha1(data).hexdigest()
    if retrieved_at is None:
        retrieved_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        basis = 'file_modification_time'
    else:
        basis = 'stated_by_user'
    lic = (entry.get('license') or catalog_license or {})
    pattern = (provider.get('browser_acquisition') or {}).get('record_url_pattern') or ''
    archive = {'provider': provider_key, 'provider_name': provider.get('name'),
               'acquisition_method': BROWSER, 'delivery': 'unknown', 'feed_id': feed_id,
               'version_id': sha1, 'record_url': pattern.format(feed_id=feed_id, sha1=sha1) if pattern else None}
    if observed_on is not None:
        archive['observed_on'] = observed_on
    return {
        'schema': ARCHIVE_SCHEMA, 'provenance_kind': THIRD_PARTY, 'source_id': source_id,
        'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
        'retrieved_at': retrieved_at, 'retrieved_at_basis': basis,
        'publisher': {'name': entry.get('publisher'), 'landing_url': entry.get('landing_url')},
        'archive': archive,
        'license': {'name': lic.get('name'), 'evidence_url': lic.get('evidence_url'),
                    'attribution': (f"Data: {entry.get('publisher')}, {lic.get('name') or 'licence unknown'}. "
                                    f"Historical version {sha1} obtained from the {provider.get('name')}; "
                                    'not endorsed by the publisher.')},
    }


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
        method = archive.get('acquisition_method', PROVIDER_DOWNLOAD)
        observed = archive.get('observed_at') or archive.get('observed_on')
        out.update(kind=THIRD_PARTY, label=KIND_LABELS[THIRD_PARTY],
                   publisher={'name': pub.get('name') or publisher, 'landing_url': pub.get('landing_url') or landing_url,
                              'reported_origin_url': pub.get('reported_origin_url')},
                   download_origin=None if method == BROWSER else archive.get('download_url'),
                   archive={**{k: archive.get(k) for k in ('provider', 'record_url', 'version_id', 'observed_at',
                                                           'observed_on', 'observed_at_evidence_url', 'download_url',
                                                           'feed_id')},
                            'provider_name': archive.get('provider_name') or _provider_name(archive.get('provider')),
                            'acquisition_method': method,
                            'delivery_known': method != BROWSER},
                   license=raw.get('license'), observed_at=observed,
                   observation_basis=('archive_record_date' if archive.get('observed_on') and not archive.get('observed_at')
                                      else 'archive_record' if observed else 'unknown'))
    elif raw is not None and raw.get('download_url'):
        out.update(kind=PUBLISHER_DIRECT, label=KIND_LABELS[PUBLISHER_DIRECT],
                   publisher={'name': publisher, 'landing_url': raw.get('landing_url') or landing_url,
                              'reported_origin_url': None},
                   download_origin=raw.get('download_url'), observed_at=snap['retrieved_at'],
                   observation_basis='local_retrieval')
    if out['kind'] == LEGACY and synthetic and out['problem'] is None:
        out.update(kind=SYNTHETIC, label=KIND_LABELS[SYNTHETIC])
    return out
