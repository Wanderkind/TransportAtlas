"""Invented third-party archive fixtures for Phase 7C tests and the History demo.

Everything here is fictional: the provider "SYNTHETIC Example Archive" and its hosts
under the reserved ``.test`` TLD do not exist, and no real archive (e.g. Transitland)
or real Sardinian feed is used. Never present results built from these as history
of a real network.
"""
from __future__ import annotations

import json
from pathlib import Path

from transport_atlas.archive_provenance import ARCHIVE_SCHEMA, THIRD_PARTY, sha1_file

PROVIDER = 'example_archive'
RECORD_HOST = 'archive.example.test'
DOWNLOAD_HOST = 'downloads.example.test'
PROVIDERS = {'providers': {PROVIDER: {
    'name': 'SYNTHETIC Example Archive (test only)', 'record_hosts': [RECORD_HOST],
    'download_hosts': [DOWNLOAD_HOST], 'version_id': 'sha1', 'import_enabled': True}}}
LICENSE = {'name': 'CC-BY-4.0', 'evidence_url': 'https://www.sardegnamobilita.it/open-data/test'}


def catalog(source_id: str, publisher: str, landing_url: str = 'https://www.sardegnamobilita.it/open-data/test',
            hosts=()) -> dict:
    """An official-style catalog naming one invented source (as the real catalog names ASPO)."""
    return {'license': dict(LICENSE), 'sources': {source_id: {
        'publisher': publisher, 'landing_url': landing_url, 'download_hosts': list(hosts)}}}


def record(zip_path: Path, source_id: str, publisher: str, *, sha256: str, retrieved_at: str,
           observed_at: str | None, landing_url: str = 'https://www.sardegnamobilita.it/open-data/test',
           reported_origin_url: str | None = 'https://www.sardegnamobilita.it/opendata/test.zip') -> dict:
    sha1 = sha1_file(zip_path)
    archive = {'provider': PROVIDER, 'provider_name': PROVIDERS['providers'][PROVIDER]['name'],
               'record_url': f'https://{RECORD_HOST}/feeds/{source_id}/versions/{sha1}',
               'version_id': sha1,
               'download_url': f'https://{DOWNLOAD_HOST}/feed_versions/{sha1}.zip',
               'observed_at': observed_at}
    if observed_at is not None:
        archive['observed_at_evidence_url'] = archive['record_url']
    return {'schema': ARCHIVE_SCHEMA, 'provenance_kind': THIRD_PARTY, 'source_id': source_id,
            'sha256': sha256, 'bytes': zip_path.stat().st_size, 'retrieved_at': retrieved_at,
            'publisher': {'name': publisher, 'landing_url': landing_url, 'reported_origin_url': reported_origin_url},
            'archive': archive,
            'license': {**LICENSE, 'attribution': f'Data: {publisher} (SYNTHETIC, test only), CC BY 4.0; '
                                                  'archived copy from SYNTHETIC Example Archive.'}}


def write(zip_path: Path, prov: dict) -> Path:
    side = Path(str(zip_path) + '.provenance.json')
    side.write_text(json.dumps(prov, indent=2), encoding='utf-8')
    return side


# -- Phase 7D1: an invented provider that accepts browser-downloaded copies ----------------------
BROWSER_PROVIDER = 'example_browser_archive'
BROWSER_PROVIDERS = {'providers': {BROWSER_PROVIDER: {
    'name': 'SYNTHETIC Example Browser Archive (test only)', 'record_hosts': [RECORD_HOST],
    'download_hosts': [], 'version_id': 'sha1', 'import_enabled': False,
    'browser_acquisition': {'enabled': True,
                            'record_url_pattern': f'https://{RECORD_HOST}/data/feeds/{{feed_id}}/versions/{{sha1}}'}}}}
FEED_ID = 'f-synthetic-valley'
OBSERVED_ON = '2026-08-28'
