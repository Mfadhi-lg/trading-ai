"""Full metadata inventory via Darwin's bulk API, without caching or sampling.

ABI: macOS SDK sys/attr.h and Apple's getattrlistbulk(2) manual.
https://github.com/apple/darwin-xnu/blob/main/bsd/man/man2/getattrlistbulk.2
"""
import ctypes
import errno
import os
from pathlib import Path
import struct
import math
import platform

NAME=0x1
TYPE=0x8
MODTIME=0x400
ERROR=0x20000000
RETURNED=0x80000000
DATALENGTH=0x200
PACK_INVALID=0x8
FUSED_TIMESTAMPS=platform.machine()=='arm64' and hasattr(math,'fma')


class Attributes(ctypes.Structure):
    _fields_=[('bitmapcount',ctypes.c_uint16),('reserved',ctypes.c_uint16),
        ('commonattr',ctypes.c_uint32),('volattr',ctypes.c_uint32),
        ('dirattr',ctypes.c_uint32),('fileattr',ctypes.c_uint32),('forkattr',ctypes.c_uint32)]


def parse_records(data,count):
    """Decode fixed packed attributes; reject missing metadata or invalid offsets."""
    position=0
    for _ in range(count):
        if position+56>len(data):raise RuntimeError('Truncated bulk metadata record')
        length=struct.unpack_from('=I',data,position)[0]
        if length<56 or position+length>len(data):raise RuntimeError('Invalid bulk metadata length')
        common,volume,directory,file_bits,fork=struct.unpack_from('=5I',data,position+4)
        if volume or directory or fork or file_bits & ~DATALENGTH:
            raise RuntimeError('Unexpected bulk attribute layout')
        error=struct.unpack_from('=I',data,position+24)[0]
        if error:raise OSError(error,os.strerror(error))
        if common & (NAME|TYPE|MODTIME)!=(NAME|TYPE|MODTIME):
            raise RuntimeError('Bulk inventory omitted required metadata')
        offset,size=struct.unpack_from('=iI',data,position+28)
        kind=struct.unpack_from('=I',data,position+36)[0]
        # File-group attributes are absent for directories, even when invalid
        # attributes are packed. The returned bitmap determines that layout.
        header_size=64 if file_bits & DATALENGTH else 56
        begin=position+28+offset
        end=begin+size
        if length<header_size or size<2 or begin<position+header_size or end>position+length:
            raise RuntimeError('Invalid bulk inventory name reference')
        raw=data[begin:end]
        if raw[-1:]!=b'\0' or b'\0' in raw[:-1] or b'/' in raw[:-1]:
            raise RuntimeError('Invalid bulk inventory filename')
        name=os.fsdecode(raw[:-1])
        if name in ('.','..'):raise RuntimeError('Unexpected relative directory entry')
        seconds,nanoseconds=struct.unpack_from('=qq',data,position+40)
        size=struct.unpack_from('=q',data,position+56)[0] if file_bits & DATALENGTH else 0
        if not 0<=nanoseconds<1_000_000_000:raise RuntimeError('Invalid bulk modification time')
        if kind==1 and (not file_bits & DATALENGTH or size<0):
            raise RuntimeError('Bulk inventory omitted regular-file size')
        # Match CPython's native stat conversion, including ARM64's fused
        # multiply-add rounding for pre-epoch modification times.
        modified=math.fma(nanoseconds,1e-9,seconds) if FUSED_TIMESTAMPS else seconds+nanoseconds*1e-9
        yield name,kind,size,modified
        position+=length


def inventory(root):
    """Return the same complete path/size/mtime mapping as the portable walker."""
    library=ctypes.CDLL(None,use_errno=True)
    try:bulk=library.getattrlistbulk
    except AttributeError:raise OSError(errno.ENOSYS,'Bulk inventory unavailable') from None
    bulk.argtypes=[ctypes.c_int,ctypes.POINTER(Attributes),ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint64]
    bulk.restype=ctypes.c_int
    attributes=Attributes(5,0,NAME|TYPE|MODTIME|ERROR|RETURNED,0,0,DATALENGTH,0)
    result={}
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    def visit(fd,relative):
        buffer=ctypes.create_string_buffer(64*1024)
        while True:
            count=bulk(fd,ctypes.byref(attributes),buffer,len(buffer),PACK_INVALID)
            if count<0:
                code=ctypes.get_errno()
                raise OSError(code,os.strerror(code),str(Path(root)/relative))
            if not count:break
            for name,kind,size,modified in parse_records(buffer.raw,count):
                child=relative/name
                if kind==5:raise RuntimeError('Storage accounting refuses symlink: '+str(Path(root)/child))
                if kind==2:
                    child_fd=os.open(name,flags,dir_fd=fd)
                    try:visit(child_fd,child)
                    finally:os.close(child_fd)
                elif kind==1:
                    result[str(child)]=(size,modified)
    fd=os.open(root,flags)
    try:visit(fd,Path())
    finally:os.close(fd)
    return result
