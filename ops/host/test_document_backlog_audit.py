import hashlib
from pathlib import Path
import tempfile
import unittest
from document_backlog_audit import verify, supported, redact_query_values


class AuditTests(unittest.TestCase):
    def test_missing_corrupt_and_hash_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'document.pdf';body=b'%PDF-test'
            row={'content_id':1,'local_path':str(path),'byte_size':len(body),'content_sha256':hashlib.sha256(body).hexdigest()}
            self.assertEqual(verify(row,(root,))['state'],'missing')
            path.write_bytes(body);self.assertEqual(verify(row,(root,))['state'],'verified')
            path.write_bytes(b'corrupt');self.assertEqual(verify(row,(root,))['state'],'corrupt')
    def test_path_escape_rejected(self):
        with self.assertRaises(RuntimeError):verify({'local_path':'/etc/passwd'},(Path('/tmp/archive'),))
    def test_media(self):
        self.assertTrue(supported({'media_type':'text/html','artifact_type':'credit_rating'}))
        self.assertFalse(supported({'media_type':'audio/mpeg','artifact_type':'recording'}))

    def test_signed_query_redacted_with_provenance_hash(self):
        secret='x'*24
        url='https://publisher.example/report.pdf?token='+secret+'&document=42'
        value=redact_query_values({'requested_url':url,'status':'PENDING'})
        self.assertNotIn(secret,value['requested_url'])
        self.assertIn('document=42',value['requested_url'])
        self.assertEqual(value['requested_url_sha256'],hashlib.sha256(url.encode()).hexdigest())
        self.assertEqual(redact_query_values(value),value)

