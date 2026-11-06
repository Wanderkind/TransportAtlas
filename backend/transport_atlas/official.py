"""Conservative official GTFS discovery and download: no guessed feed URLs.

The official Sardegna Mobilità pages expose datasets with irregular updates. We
store verified *landing* URLs in the manifest, discover resources at run time and
preserve the actual final download URL plus the bytes' SHA-256.

This module deliberately refuses unknown hosts, redirect targets, oversized files,
and HTML masquerading as a ZIP. No downloaded data is committed to source control.
"""
from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import zipfile
from datetime import datetime, timezone
from uuid import uuid4

from .gtfs import FeedError, MAX_COMPRESSED

DEFAULT_CATALOG = Path(__file__).resolve().parents[2] / 'configs' / 'sardinia_sources.json'
ALLOWED_OFFICIAL_HOSTS = {'sardegnamobilita.it', 'www.sardegnamobilita.it'}


def safe_url(url: str, *, allowed_hosts: set[str] | None = None) -> str:
    p = urlsplit(url)
    host = (p.hostname or '').lower().rstrip('.')
    hosts = ALLOWED_OFFICIAL_HOSTS if allowed_hosts is None else allowed_hosts
    if p.scheme != 'https' or not host or host not in hosts:
        raise FeedError('Resource URL must use HTTPS on an explicitly approved official or operator host')
    if p.username or p.password or p.port not in (None, 443):
        raise FeedError('Resource URL includes unsupported credentials or port')
    return url


class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a' and self.current is None:
            attr = dict(attrs)
            self.current = {'href': attr.get('href', ''), 'text': '', 'title': attr.get('title', '')}

    def handle_data(self, text):
        if self.current is not None:
            self.current['text'] += text

    def handle_endtag(self, tag):
        if tag.lower() == 'a' and self.current is not None:
            self.items.append(self.current)
            self.current = None


def candidates(html: str, landing_url: str) -> list[dict]:
    """Rank real anchors, not website icons or invented URLs.

    Discovery may return no candidate; that is an explicit diagnostic, not a
    reason to guess a URL or silently substitute another transport provider.
    """
    safe_url(landing_url)
    parser = _Anchors()
    parser.feed(html)
    results = []
    seen = set()
    for item in parser.items:
        raw = item['href'].strip()
        if not raw or raw.startswith(('#', 'javascript:', 'mailto:')):
            continue
        url = urljoin(landing_url, raw)
        try:
            # External resource anchors are discovery evidence only; download
            # authorization is separately source-scoped via the manifest.
            safe_url(url, allowed_hosts={(urlsplit(url).hostname or '').lower()})
        except FeedError:
            continue
        path = urlsplit(url).path.lower()
        label = ' '.join((item['text'] + ' ' + item['title']).lower().split())
        if path.endswith(('.svg', '.png', '.gif', '.jpeg', '.jpg', '.css', '.js')):
            continue
        score = 0
        if path.endswith('.zip'): score += 12
        if 'gtfs' in path: score += 6
        if 'download' in path or '/file/' in path: score += 3
        if 'gtfs' in label: score += 6
        if 'quadri orari' in label: score += 3
        if 'risorsa' in label or 'scarica' in label: score += 1
        # Links back to the landing page, other tags and category pages are not feeds.
        if score < 6 or url.rstrip('/') == landing_url.rstrip('/'):
            continue
        if url in seen:
            continue
        seen.add(url)
        results.append({'url': url, 'label': item['text'].strip(), 'score': score})
    return sorted(results, key=lambda x: (-x['score'], x['url']))


class _OfficialRedirects(HTTPRedirectHandler):
    def __init__(self, allowed_hosts=None):
        super().__init__()
        self.allowed_hosts = ALLOWED_OFFICIAL_HOSTS if allowed_hosts is None else allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url(newurl, allowed_hosts=self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(allowed_hosts=None):
    return build_opener(_OfficialRedirects(allowed_hosts))


def _get(url: str, *, timeout: int = 30, max_bytes: int = 3_000_000) -> tuple[bytes, str]:
    safe_url(url)
    request = Request(url, headers={'User-Agent': 'TransportAtlas/0.3 (GTFS research; local importer)', 'Accept': '*/*'})
    with _opener().open(request, timeout=timeout) as response:
        final_url = safe_url(response.geturl())
        length = response.headers.get('Content-Length')
        if length and int(length) > max_bytes:
            raise FeedError('Official response exceeded permitted size before download')
        content = response.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise FeedError('Official response exceeded permitted size')
        return content, final_url


def catalog(catalog_file: Path = DEFAULT_CATALOG) -> dict:
    data = json.loads(Path(catalog_file).read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not isinstance(data.get('sources'), dict):
        raise FeedError('Invalid official feed manifest')
    for src in data['sources'].values():
        safe_url(src['landing_url'])
        hosts = src.get('download_hosts', [])
        if not isinstance(hosts,list) or any(not isinstance(h,str) or not h or '/' in h or ':' in h for h in hosts):
            raise FeedError('Invalid operator download host allowlist')
    return data


def discover(source: str, *, catalog_file: Path = DEFAULT_CATALOG) -> dict:
    entry = catalog(catalog_file)['sources'].get(source)
    if entry is None:
        raise FeedError(f'Unknown official source: {source}')
    page, final_url = _get(entry['landing_url'])
    links = candidates(page.decode('utf-8-sig', errors='replace'), final_url)
    return {'source_id': source, 'dataset_id': entry['official_dataset_id'],
            'landing_url': final_url, 'resource_candidates': links}


def download(source: str, destination: Path, *, catalog_file: Path = DEFAULT_CATALOG,
             resource_url: str | None = None, timeout: int = 60) -> dict:
    """Download into a temporary ZIP, verify magic and members, atomically replace."""
    entry = catalog(catalog_file)['sources'].get(source)
    if entry is None:
        raise FeedError(f'Unknown official source: {source}')
    discovered = discover(source, catalog_file=catalog_file) if resource_url is None else None
    allowed = ALLOWED_OFFICIAL_HOSTS | set(entry.get('download_hosts',[]))
    links = ([{'url': safe_url(resource_url, allowed_hosts=allowed), 'label': 'operator supplied official download URL', 'score': 99}]
             if resource_url else discovered['resource_candidates'])
    if not links:
        raise FeedError('Official page has no discoverable GTFS download link. Download the ZIP manually from the official page.')
    failures = []
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Try alternatives only when the server returns a non-feed, not when a ZIP fails
    # content validation. A superficially valid ZIP from a different source is unsafe.
    for item in links[:8]:
        temp = destination.with_name(destination.name + '.' + uuid4().hex + '.part')
        try:
            url = safe_url(item['url'], allowed_hosts=allowed)
            h = hashlib.sha256()
            total = 0
            req = Request(url, headers={'User-Agent': 'TransportAtlas/0.3 (GTFS research; local importer)'})
            with _opener(allowed).open(req, timeout=timeout) as stream, temp.open('wb') as output:
                final = safe_url(stream.geturl(), allowed_hosts=allowed)
                n = stream.headers.get('Content-Length')
                if n and int(n) > MAX_COMPRESSED:
                    raise FeedError('GTFS ZIP too large')
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk: break
                    total += len(chunk)
                    if total > MAX_COMPRESSED:
                        raise FeedError('GTFS ZIP too large')
                    output.write(chunk)
                    h.update(chunk)
            with temp.open('rb') as f:
                if f.read(4) != b'PK\x03\x04':
                    raise FeedError('Official resource did not return a ZIP')
            with zipfile.ZipFile(temp) as z:
                names = {p.filename.lower().split('/')[-1] for p in z.infolist()}
                if not {'agency.txt', 'routes.txt', 'stops.txt', 'trips.txt', 'stop_times.txt'}.issubset(names):
                    raise FeedError('Official ZIP is missing mandatory GTFS files')
            temp.replace(destination)
            meta = {'source_id': source, 'dataset_id': entry['official_dataset_id'],
                    'landing_url': entry['landing_url'], 'download_url': final,
                    'retrieved_at': datetime.now(timezone.utc).isoformat(),
                    'sha256': h.hexdigest(), 'bytes': total,
                    'license': 'not asserted: review publisher terms before redistribution'}
            meta_file = destination.with_suffix(destination.suffix + '.provenance.json')
            meta_file.write_text(json.dumps(meta, indent=2), encoding='utf-8')
            return meta
        except Exception as exc:
            failures.append({'url': item['url'], 'error': str(exc)[:250]})
        finally:
            temp.unlink(missing_ok=True)
    raise FeedError('Unable to download a valid GTFS feed from official links: ' + json.dumps(failures))
