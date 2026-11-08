"""Verify effective network isolation, allowing dormant kernel tunnel devices."""
import fcntl
import json
import socket
import struct


def assert_isolated(interfaces=None,flags=None):
    interfaces=socket.if_nameindex() if interfaces is None else interfaces
    def read_flags(name):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            result=fcntl.ioctl(sock.fileno(),0x8913,struct.pack('256s',name.encode()[:15]))
        return struct.unpack_from('H',result,16)[0]
    flags=read_flags if flags is None else flags
    observed=[]
    for _,name in interfaces:
        value=flags(name)
        observed.append({'interface':name,'flags':value})
        # IFF_UP=1, IFF_LOOPBACK=8. With cap_drop=ALL a worker cannot bring
        # dormant devices up. Docker network_mode=none is independently pinned.
        if value&1 and not value&8:
            raise RuntimeError('Local historical processing has an active external network interface: '+name)
    if not observed:raise RuntimeError('Cannot verify local network namespace')
    return observed


if __name__=='__main__':print(json.dumps({'status':'PASS','interfaces':assert_isolated()}))
