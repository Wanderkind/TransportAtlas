"""The downloader is tested offline; CI live probes are separately reported."""
from pathlib import Path
from unittest.mock import patch
import io
import json
import zipfile
import pytest
from transport_atlas import official
from transport_atlas.gtfs import FeedError

PAGE='''<html><a href="/open-data/quadri-orari-aspo">Quadri orari ASPO</a>
<a href="/sites/default/files/icons/file-gtfs.svg">gtfs icon</a>
<a href="/sites/default/files/aspo-2026.zip">Quadri orari ASPO (GTFS)</a>
<a href="https://attacker.example/aspo.zip">other</a></html>'''


def test_manifest_and_landing_pages():
    cfg=official.catalog()
    assert len(cfg['sources']) == 4
    for item in cfg['sources'].values():
        official.safe_url(item['landing_url'])
        assert item['official_dataset_id'].startswith('R_SARDEG:')


def test_discovery_respects_origin_and_avoids_icon():
    items=official.candidates(PAGE,'https://www.sardegnamobilita.it/open-data/quadri-orari-aspo')
    assert len(items)==2
    # The source page can mention third-party links; only the manifest may authorize downloads.
    assert items[0]['url'].endswith('/aspo-2026.zip')
    assert items[0]['score']>=12


def test_unsafe_redirects_or_embedded_credentials_rejected():
    for url in ['http://www.sardegnamobilita.it/a.zip',
        'https://evil.example/a.zip',
        'https://sardegnamobilita.it.evil.example/a.zip',
        'https://user:pass@sardegnamobilita.it/a.zip']:
        with pytest.raises(FeedError):official.safe_url(url)
    with pytest.raises(FeedError):official._OfficialRedirects().redirect_request(
        None,None,302,'redirect',{},'https://evil.example/')


def test_catalog_invalid(tmp_path):
    x=tmp_path/'x.json'
    x.write_text('{"sources": {}, "schema_version": 2}')
    with pytest.raises(FeedError):official.catalog(x)


def test_discover_explicit_failure_when_page_has_no_resources():
    with patch.object(official,'_get',return_value=(b'<html><body>GTFS not available</body></html>', 'https://www.sardegnamobilita.it/open-data/quadri-orari-aspo')):
        result=official.discover('aspo')
    assert result['resource_candidates']==[]


class FakeResponse:
    def __init__(self, data):self.f=io.BytesIO(data);self.headers={'Content-Length':str(len(data))}
    def geturl(self):return 'https://www.sardegnamobilita.it/files/aspo.zip'
    def read(self,n=-1):return self.f.read(n)
    def __enter__(self):return self
    def __exit__(self,*_):self.f.close()


def test_download_uses_real_zip_validation_and_writes_provenance(tmp_path):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        for name in ('agency','routes','trips','stops','stop_times'):
            z.writestr(name+'.txt','sample\n')
    output=tmp_path/'aspo.zip'
    with patch.object(official,'_opener') as opener:
        opener.return_value.open.return_value=FakeResponse(b.getvalue())
        metadata=official.download('aspo',output,resource_url='https://www.sardegnamobilita.it/files/aspo.zip')
    assert output.read_bytes()==b.getvalue()
    assert metadata['source_id']=='aspo'
    assert json.loads(output.with_suffix('.zip.provenance.json').read_text())['sha256']==metadata['sha256']


def test_download_refuses_html_not_zip(tmp_path):
    dest=tmp_path/'test.zip'
    with patch.object(official,'_opener') as opener:
        opener.return_value.open.return_value=FakeResponse(b'<html>no</html>')
        with pytest.raises(FeedError):official.download('aspo',dest,resource_url='https://www.sardegnamobilita.it/files/aspo.zip')
    assert not dest.exists()


def test_operator_host_is_explicitly_scoped():
    hosts = official.ALLOWED_OFFICIAL_HOSTS | set(official.catalog()['sources']['aspo']['download_hosts'])
    assert official.safe_url('https://www.aspo.it/wp-content/uploads/open_data.zip', allowed_hosts=hosts)
    with pytest.raises(FeedError):
        official.safe_url('https://atpsassari.it/media/files/atpss-gtfs.zip', allowed_hosts=hosts)


def test_arst_host_verified_in_official_catalog():
    hosts=official.ALLOWED_OFFICIAL_HOSTS | set(official.catalog()['sources']['arst']['download_hosts'])
    assert official.safe_url('https://www.arstspa.info/arst-cagliari-it.zip', allowed_hosts=hosts)
