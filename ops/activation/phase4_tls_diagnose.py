"""Bounded TLS handshakes only: no HTTP document requests or production mounts."""
import certifi,hashlib,json,os,re,ssl,subprocess
from pathlib import Path
HOST='www.sacheerome.com'
def run(args):
    try:
        p=subprocess.run(args,input='',text=True,capture_output=True,timeout=15)
        return {'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
    except subprocess.TimeoutExpired:return {'exit_code':'timeout'}
paths={'certifi':certifi.where(),'system':'/etc/ssl/certs/ca-certificates.crt'}
result={'openssl':ssl.OPENSSL_VERSION,'certifi_version':certifi.__version__,'ssl_default_paths':ssl.get_default_verify_paths()._asdict(),
 'environment':{k:({'set':True} if 'PROXY' in k.upper() else os.environ[k]) for k in ('HTTPS_PROXY','HTTP_PROXY','ALL_PROXY','https_proxy','SSL_CERT_FILE','SSL_CERT_DIR','REQUESTS_CA_BUNDLE','CURL_CA_BUNDLE') if k in os.environ},'bundles':{},'handshakes':{}}
for label,path in paths.items():
    if not Path(path).exists():continue
    body=Path(path).read_bytes();result['bundles'][label]={'path':path,'sha256':hashlib.sha256(body).hexdigest(),'certificate_count':body.count(b'BEGIN CERTIFICATE')}
    result['handshakes'][label]=run(['openssl','s_client','-connect',HOST+':443','-servername',HOST,'-showcerts','-verify_return_error','-verify_hostname',HOST,'-purpose','sslserver','-CAfile',path])
print(json.dumps(result))
