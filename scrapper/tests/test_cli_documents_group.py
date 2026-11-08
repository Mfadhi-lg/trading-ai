"""Document adapter contracts: delegation, outputs, refusal and connection ownership."""
import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from scrapper import cli
from scrapper.cli_groups.documents import COMMANDS

# Each public command and its existing service owner, with minimum valid arguments.
CASES = {
 'document-quality-report': ('write_document_quality_report', []),
 'reconcile-artifacts': ('reconcile_artifacts', ['--due']),
 'backfill-result-artifacts': ('backfill_accepted_result_artifacts', []),
 'artifact-coverage': ('write_artifact_coverage_report', []),
 'discover-documents': ('discover_documents', ['--symbol','TEST','--manifest','/tmp/manifest.json']),
 'promote-document-links': ('promote_document_links', []),
 'build-document-recovery-targets': ('build_recovery_targets', []),
 'run-document-recovery-worker': ('run_document_recovery_worker', ['--source','nse']),
 'document-alternate-source-recovery-report': ('write_alternate_source_recovery_report', []),
 'reconcile-promoted-artifacts': ('reconcile_promoted_artifacts', []),
 'queue-credit-rating-downloads': ('queue_credit_rating_downloads', []),
 'queue-document-downloads': ('queue_document_downloads', ['--manifest','/tmp/manifest.json']),
 'queue-accepted-document-downloads': ('enqueue_documents_for_all_accepted_parses', []),
 'run-document-download-worker': ('run_document_download_worker', ['--scope-id','test']),
 'document-recovery-report': ('write_document_recovery_report', []),
 'requeue-document-downloads': ('requeue_document_downloads', ['--queue-kind','generic','--job-id','7','--reason-code','TRANSIENT_SOURCE_FAILURE']),
 'register-document-replacement': ('register_replacement_document_url', ['--artifact-id','7','--replaces-source-id','8','--url','https://example.com/a.pdf','--reason-code','OPERATOR_VERIFIED']),
 'import-manual-document': ('import_manual_document', ['--artifact-id','7','--source-id','8','--file','/tmp/a.pdf','--official','--reason-detail','verified']),
 'document-storage-projection': ('write_document_storage_projection', ['--scope-id','test']),
 'run-credit-rating-download-worker': ('run_credit_rating_download_worker', []),
 'fetch-documents': ('fetch_documents', ['--symbol','TEST']),
 'extract-document': ('extract_document', ['--artifact-id','7']),
 'normalize-credit-rating': ('normalize_credit_rating_document', ['--artifact-id','7']),
 'ingest-official-transcript': ('ingest_official_transcript', ['--artifact-id','7']),
 'normalize-recording': ('normalize_recording', ['--artifact-id','7']),
 'transcribe': ('transcribe_recording', ['--artifact-id','7','--model-path','/tmp/model']),
}

class DocumentCliTests(unittest.TestCase):
 def invoke(self, argv):
  output=io.StringIO()
  with contextlib.redirect_stdout(output): status=cli.main(argv)
  return status,json.loads(output.getvalue())

 def test_exact_membership(self):
  self.assertEqual(set(COMMANDS),set(CASES))

 def test_every_command_delegates_and_preserves_error_output(self):
  for command,(owner,flags) in CASES.items():
   with self.subTest(command=command),patch.object(cli,'connect',return_value=Mock()) as connect,patch.object(cli,'init_db'),patch.object(cli,owner,side_effect=RuntimeError('refused')) as call:
    self.assertEqual(self.invoke([command,*flags]),(2,{'status':'error','error':'refused'}))
    call.assert_called_once()
    if connect.called: connect.return_value.close.assert_called_once_with()

 def test_connection_modes_reports_and_exit_states(self):
  with tempfile.TemporaryDirectory() as directory:
   output=Path(directory)/'report.json'
   for command,owner in [('document-quality-report','write_document_quality_report'),('artifact-coverage','write_artifact_coverage_report')]:
    for state in ('complete','incomplete'):
     payload={'coverage_status':state,'queue_status':'complete','pilot':{'status':state},'summary':{}}
     output.write_text(json.dumps(payload)); conn=Mock()
     with patch.object(cli,'connect',return_value=conn) as connect,patch.object(cli,'init_db'),patch.object(cli,owner,return_value=output):
      status,result=self.invoke([command,'--output',str(output)])
      self.assertEqual(status,0 if command=='artifact-coverage' or state=='complete' else 1)
      self.assertEqual(result['output'],str(output.resolve()))
      connect.assert_called_once_with(cli.DB_PATH,read_only=True); conn.close.assert_called_once_with()

 def test_worker_scope_policy_outcome_and_bounds(self):
  for selection,scope in [(['--scope-id','test'],'test'),(['--all-scopes'],None)]:
   for failures in (0,1):
    conn=Mock(); payload={'outcome_counts':{'WORKER_EXCEPTION':failures}}
    with patch.object(cli,'connect',return_value=conn),patch.object(cli,'init_db'),patch.object(cli,'run_document_download_worker',return_value=payload) as worker,patch.object(cli,'write_download_queue_report',return_value=({'bytes':{'stored':7}},Path('/tmp/report.json'))):
     status,result=self.invoke(['run-document-download-worker',*selection,'--limit','2','--max-attempts','4'])
     self.assertEqual(status,failures); self.assertEqual(result['byte_report'],{'stored':7})
     self.assertEqual(worker.call_args.kwargs['scope_id'],scope); self.assertEqual(worker.call_args.kwargs['max_attempts'],4)
     self.assertEqual(worker.call_args.kwargs['limit'],2); conn.close.assert_called_once_with()
  for command,flags in [('run-document-download-worker',['--scope-id','test']),('run-credit-rating-download-worker',[]),('fetch-documents',['--symbol','TEST'])]:
   for option,value in [('--max-bytes',cli.DOCUMENT_MAX_RESPONSE_BYTES+1),('--timeout-seconds',cli.DOCUMENT_TOTAL_TIMEOUT_SECONDS+1)]:
    with patch.object(cli,'connect') as connect:
     self.assertEqual(self.invoke([command,*flags,option,str(value)])[0],2); connect.assert_not_called()

 def test_replacement_and_manual_import_preserve_evidence(self):
  for command in ('register-document-replacement','import-manual-document'):
   owner,flags=CASES[command]; conn=Mock()
   with patch.object(cli,'connect',return_value=conn),patch.object(cli,'init_db'),patch.object(cli,owner,return_value={'attached':True}) as call:
    self.assertEqual(self.invoke([command,*flags,'--evidence','reviewed']),(0,{'status':'ok','attached':True}))
    self.assertEqual(call.call_args.kwargs['evidence'],{'operator_note':'reviewed'})
    self.assertEqual(call.call_args.kwargs['artifact_id'],7); conn.close.assert_called_once_with()

 def test_normalization_transcript_extraction_and_transcription_outcomes(self):
  for command,key,field in [('normalize-credit-rating','normalization','normalization_status'),('normalize-recording','normalization','normalization_status'),('ingest-official-transcript','transcript','validation_status'),('extract-document','extraction','extraction_status'),('transcribe','transcription','run_status')]:
   owner,flags=CASES[command]
   accepted='ok' if command=='extract-document' else 'accepted'
   for state,expected in [(accepted,0),('unknown',1)]:
    payload={key:{field:state}}
    with patch.object(cli,owner,return_value=payload): self.assertEqual(self.invoke([command,*flags]),(expected,{'status':'ok',**payload}))
  with patch.object(cli,'transcribe_recording',return_value={'transcription':None}): self.assertEqual(self.invoke(['transcribe',*CASES['transcribe'][1]])[0],0)

 def test_storage_validation_refuses_before_connection(self):
  for value in ('bad','unknown=3','credit_rating=bad'):
   with patch.object(cli,'connect') as connect:
    self.assertEqual(self.invoke(['document-storage-projection','--scope-id','test','--planned-target',value])[0],2); connect.assert_not_called()

 def test_output_guard_precedes_document_dispatch(self):
  with patch.object(cli,'assert_configured_writable_targets',side_effect=RuntimeError('production refused')),patch.object(cli,'extract_document') as owner:
   with self.assertRaisesRegex(RuntimeError,'production refused'): cli.main(['extract-document','--artifact-id','7','--output','/tmp/refused.json'])
   owner.assert_not_called()

 def test_sqlite_error_contract_is_not_widened(self):
  with patch.object(cli,'connect',side_effect=sqlite3.OperationalError('locked')):
   self.assertEqual(self.invoke(['queue-credit-rating-downloads'])[0],2)
  with patch.object(cli,'discover_documents',side_effect=sqlite3.OperationalError('locked')):
   with self.assertRaises(sqlite3.OperationalError): self.invoke(['discover-documents',*CASES['discover-documents'][1]])
