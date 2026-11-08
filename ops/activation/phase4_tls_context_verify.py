"""TLS-only test of the actual downloader context; no document HTTP request."""
import json,socket,ssl
from unittest.mock import patch
import certifi
from scrapper.documents.download.tls import verified_chain_context,INTERMEDIATE
result={}
def handshake(context, hostname):
 try:
  with socket.create_connection(('www.sacheerome.com',443),timeout=10) as sock:
   with context.wrap_socket(sock,server_hostname=hostname) as secured:
    return {'status':'PASS','peer':secured.getpeercert(),'check_hostname':context.check_hostname,'verify_mode':context.verify_mode,'partial_chain':bool(context.verify_flags&ssl.VERIFY_X509_PARTIAL_CHAIN)}
 except ssl.SSLError as e:return {'status':'REJECTED','error':str(e)}
result['actual_context']=handshake(verified_chain_context(),'www.sacheerome.com')
result['wrong_hostname']=handshake(verified_chain_context(),'wrong.example')
with patch.object(certifi,'where',return_value=str(INTERMEDIATE)):
 result['intermediate_without_existing_root']=handshake(verified_chain_context(),'www.sacheerome.com')
assert result['actual_context']['status']=='PASS' and not result['actual_context']['partial_chain']
assert result['wrong_hostname']['status']=='REJECTED'
assert result['intermediate_without_existing_root']['status']=='REJECTED'
print(json.dumps(result))
