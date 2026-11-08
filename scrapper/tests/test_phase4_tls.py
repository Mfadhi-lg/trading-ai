"""Offline real-TLS regression: verified intermediate, no new trust anchor."""
import hashlib,socket,ssl,subprocess,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
import certifi,requests
from scrapper.documents.download import tls
from scrapper.documents.downloader import download_url,DownloadPolicy

class TLSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        def run(*args):subprocess.run(['openssl',*args],cwd=cls.root,check=True,capture_output=True)
        (cls.root/'root.cnf').write_text('[req]\ndistinguished_name=dn\nx509_extensions=ca\n[dn]\nCN=Fixture Root\n[ca]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n')
        run('req','-config','root.cnf','-x509','-newkey','rsa:2048','-nodes','-keyout','root.key','-out','root.pem','-subj','/CN=Fixture Root','-days','2')
        run('req','-newkey','rsa:2048','-nodes','-keyout','intermediate.key','-out','intermediate.csr','-subj','/CN=Fixture Intermediate')
        (cls.root/'ca.ext').write_text('basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n')
        run('x509','-req','-in','intermediate.csr','-CA','root.pem','-CAkey','root.key','-CAcreateserial','-out','intermediate.pem','-days','2','-extfile','ca.ext')
        run('req','-newkey','rsa:2048','-nodes','-keyout','leaf.key','-out','leaf.csr','-subj','/CN=www.sacheerome.com')
        (cls.root/'leaf.ext').write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:www.sacheerome.com\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n')
        run('x509','-req','-in','leaf.csr','-CA','intermediate.pem','-CAkey','intermediate.key','-CAcreateserial','-out','leaf.pem','-days','2','-extfile','leaf.ext')
        cls.intermediate=cls.root/'intermediate.pem'
        cls.digest=hashlib.sha256(cls.intermediate.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_pinned_certificate_and_exact_host_scope(self):
        with requests.Session() as session:
            tls.install_verified_chain(session,'https://www.sacheerome.com.evil.test/')
            self.assertNotIsInstance(session.get_adapter('https://www.sacheerome.com.evil.test/'),tls.VerifiedChainAdapter)
            tls.install_verified_chain(session,'https://www.sacheerome.com/document.pdf')
            context=session.get_adapter('https://www.sacheerome.com/document.pdf').chain_context
            self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)
            self.assertFalse(context.verify_flags&ssl.VERIFY_X509_PARTIAL_CHAIN)
        with patch.object(tls,'INTERMEDIATE_SHA256','wrong'):
            with self.assertRaises(ssl.SSLError):tls.verified_chain_context()

    def test_certificate_errors_remain_failed_downloads_with_original_limits(self):
        with requests.Session() as session,patch.object(session,'get',side_effect=requests.exceptions.SSLError('certificate verify failed')) as get:
            result=download_url('https://www.sacheerome.com/document.pdf',artifact_type='transcript',session=session,
                policy=DownloadPolicy(max_bytes=10485760,max_redirects=0))
            self.assertEqual(result.result_status,'network_error');self.assertIsNone(result.body)
            self.assertEqual(get.call_count,1);self.assertEqual(get.call_args.kwargs['verify'],certifi.where())
            self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def context(self,roots=None):
        with patch.object(tls,'INTERMEDIATE',self.intermediate),patch.object(tls,'INTERMEDIATE_SHA256',self.digest),patch.object(certifi,'where',return_value=str(roots or self.root/'root.pem')):
            return tls.verified_chain_context()

    def exchange(self,context,hostname='www.sacheerome.com',request=False):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(4)
        port=listener.getsockname()[1];server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.load_cert_chain(self.root/'leaf.pem',self.root/'leaf.key')
        def serve():
            try:
                conn,_=listener.accept()
                with conn:
                    try:
                        with server_context.wrap_socket(conn,server_side=True) as server:
                            if request:
                                server.recv(4096);body=b'%PDF-1.4\nfixture\n%%EOF'
                                server.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/pdf\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body)
                    except ssl.SSLError:pass
            finally:listener.close()
        thread=threading.Thread(target=serve);thread.start()
        try:
            if request:
                original=socket.getaddrinfo
                def dns(host,*args,**kwargs):return original('127.0.0.1' if host=='www.sacheerome.com' else host,*args,**kwargs)
                with requests.Session() as session,patch.object(tls,'verified_chain_context',return_value=context),patch.object(certifi,'where',return_value=str(self.root/'root.pem')),patch('socket.getaddrinfo',side_effect=dns):
                    session.trust_env=False
                    return download_url(f'https://www.sacheerome.com:{port}/document.pdf',artifact_type='transcript',session=session,policy=DownloadPolicy(connect_timeout_seconds=2,read_timeout_seconds=2,max_redirects=0))
            with socket.create_connection(('127.0.0.1',port),timeout=2) as client:
                with context.wrap_socket(client,server_hostname=hostname):return True
        finally:thread.join(5);self.assertFalse(thread.is_alive())

    def test_real_downloader_completes_omitted_intermediate_with_trusted_root(self):
        result=self.exchange(self.context(),request=True)
        self.assertEqual(result.result_status,'validated',result.error_message)

    def test_missing_intermediate_wrong_hostname_and_missing_root_fail_closed(self):
        base=ssl.create_default_context(cafile=str(self.root/'root.pem'));base.verify_flags &= ~ssl.VERIFY_X509_PARTIAL_CHAIN
        with self.assertRaises(ssl.SSLCertVerificationError):self.exchange(base)
        with self.assertRaises(ssl.SSLCertVerificationError):self.exchange(self.context(),'wrong.example')
        with self.assertRaises(ssl.SSLCertVerificationError):self.exchange(self.context(roots=self.intermediate))
