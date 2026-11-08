"""Verify issuer chain to existing Certifi roots; bounded TLS handshakes only."""
import certifi,json,ssl,socket,subprocess,tempfile
from pathlib import Path
root=Path('/evidence')
def run(args):
 p=subprocess.run(args,text=True,capture_output=True,timeout=15);return {'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
results={}
results['intermediate_chain']=run(['openssl','verify','-CAfile',certifi.where(),str(root/'rapidssl-tls-rsa-ca-g1.pem')])
results['leaf_chain_hostname']=run(['openssl','verify','-purpose','sslserver','-verify_hostname','www.sacheerome.com','-CAfile',certifi.where(),'-untrusted',str(root/'rapidssl-tls-rsa-ca-g1.pem'),str(root/'issuer-leaf.pem')])
results['wrong_hostname']=run(['openssl','verify','-purpose','sslserver','-verify_hostname','wrong.example','-CAfile',certifi.where(),'-untrusted',str(root/'rapidssl-tls-rsa-ca-g1.pem'),str(root/'issuer-leaf.pem')])
with tempfile.TemporaryDirectory() as temp:
 bundle=Path(temp)/'bundle.pem';bundle.write_bytes(Path(certifi.where()).read_bytes()+b'\n'+(root/'rapidssl-tls-rsa-ca-g1.pem').read_bytes())
 results['supplemented_handshake']=run(['openssl','s_client','-connect','www.sacheerome.com:443','-servername','www.sacheerome.com','-verify_return_error','-verify_hostname','www.sacheerome.com','-purpose','sslserver','-CAfile',str(bundle)])
 results['intermediate_not_root']=run(['openssl','verify','-CAfile',str(root/'rapidssl-tls-rsa-ca-g1.pem'),str(root/'issuer-leaf.pem')])
print(json.dumps(results))
