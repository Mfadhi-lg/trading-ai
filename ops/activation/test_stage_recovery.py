"""Fail-closed image inputs and bounded document timeout cleanup."""
import hashlib,json,subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
import phase4_schedule as documents
import step3_financial as financial


def completed(code=0,stdout='',stderr=''):
    return SimpleNamespace(returncode=code,stdout=stdout,stderr=stderr)


def container(state='running',project='trading-ai-phase4-routine'):
    return json.dumps([{'Id':'owned-id','Config':{'Labels':{'com.docker.compose.project':project,
        'com.docker.compose.service':'document-once'}},'State':{'Status':state}}])


def test_financial_preserves_image_ingest_hash_and_does_not_mount_document_overlay(tmp_path):
    with patch.object(financial,'OUT',tmp_path),patch.object(financial.nse,'check_configuration'):
        financial.prepare()
    manifest=json.loads((tmp_path/'manifest.json').read_text())
    source=json.loads(financial.nse.SOURCE.read_text())
    expected=next(r['sha256'] for r in source['files'] if r['path']=='scrapper/workflows/screener/ingest.py')
    assert manifest['application_hashes']['scrapper/workflows/screener/ingest.py']==expected
    assert 'scrapper/workflows/screener/ingest.py' not in manifest['application_overlays']
    compose=json.loads((tmp_path/'compose.json').read_text())
    assert not any(m['target']=='/app/scrapper/workflows/screener/ingest.py'
        for m in compose['services']['current-financial-once']['volumes'])
    assert hashlib.sha256((financial.ROOT/'scrapper/workflows/screener/ingest.py').read_bytes()).hexdigest()!=expected


def test_financial_refuses_unsealed_host_document_source(tmp_path):
    loads=json.loads
    def changed(value,*a,**k):
        result=loads(value,*a,**k)
        if isinstance(result,dict) and 'phase' in result and 'files' in result:
            result['files']['scrapper/workflows/screener/ingest.py']='unverified'
        return result
    with patch.object(financial,'OUT',tmp_path),patch.object(financial.nse,'check_configuration'),patch.object(financial.json,'loads',side_effect=changed):
        with pytest.raises(RuntimeError,match='Sealed document-only ingestion source changed'):
            financial.prepare()


def test_timeout_before_creation_keeps_original_failure_without_stop(tmp_path):
    with patch.object(documents.subprocess,'run',return_value=completed(1,stderr='Error: No such object: trading-ai-phase4-routine')) as run:
        documents.cleanup_timed_out_container(tmp_path)
    assert run.call_count==1
    assert json.loads((tmp_path/'timeout-cleanup.json').read_text())['status']=='not_created'


def test_timeout_does_not_treat_unavailable_engine_as_absent(tmp_path):
    with patch.object(documents.subprocess,'run',return_value=completed(1,stderr='Cannot connect to Docker daemon')):
        with pytest.raises(RuntimeError,match='Cannot verify'):documents.cleanup_timed_out_container(tmp_path)
    assert not (tmp_path/'timeout-cleanup.json').exists()


def test_timeout_refuses_foreign_container(tmp_path):
    with patch.object(documents.subprocess,'run',return_value=completed(stdout=container(project='another-project'))) as run:
        with pytest.raises(RuntimeError,match='ownership differs'):documents.cleanup_timed_out_container(tmp_path)
    assert run.call_count==1


def test_timeout_stops_exact_owned_id_then_verifies_auto_removal(tmp_path):
    responses=[completed(stdout=container()),completed(),completed(1,stderr='Error: No such object: owned-id')]
    with patch.object(documents.subprocess,'run',side_effect=responses) as run:
        documents.cleanup_timed_out_container(tmp_path)
    assert run.call_args_list[1].args[0]==['docker','stop','-t','15','owned-id']
    assert json.loads((tmp_path/'timeout-cleanup.json').read_text())['terminated']


def test_timeout_cannot_release_owner_with_live_container(tmp_path):
    responses=[completed(stdout=container()),completed(),completed(stdout=container())]
    with patch.object(documents.subprocess,'run',side_effect=responses):
        with pytest.raises(RuntimeError,match='still active'):documents.cleanup_timed_out_container(tmp_path)


def test_unstarted_container_removed_without_force_or_volume_deletion(tmp_path):
    responses=[completed(stdout=container('created')),completed(stdout=container('created')),completed()]
    with patch.object(documents.subprocess,'run',side_effect=responses) as run:
        documents.cleanup_timed_out_container(tmp_path)
    assert run.call_args_list[-1].args[0]==['docker','rm','owned-id']


@pytest.mark.parametrize('message',[
    'error: no such object: trading-ai-phase4-routine',
    'Error response from daemon: No such container: trading-ai-phase4-routine\n',
])
def test_lowercase_missing_owned_container_is_not_cleanup_failure(tmp_path,message):
    with patch.object(documents.subprocess,'run',return_value=completed(1,stderr=message)):
        documents.cleanup_timed_out_container(tmp_path)
    assert json.loads((tmp_path/'timeout-cleanup.json').read_text())['status']=='not_created'


@pytest.mark.parametrize('message',[
    'error: no such object: foreign-container',
    'error: no such object: trading-ai-phase4-routine\nCannot connect to Docker daemon',
])
def test_absence_diagnostic_must_be_exact_and_exclusive(tmp_path,message):
    with patch.object(documents.subprocess,'run',return_value=completed(1,stderr=message)):
        with pytest.raises(RuntimeError,match='Cannot verify'):documents.cleanup_timed_out_container(tmp_path)
