"""Bounded V3 Protobuf wire projection of the vendored official schema.

Decode only equity FullFeed/LTPC/OHLC and market status; safely skip other
fields. No JSON fallback or V2 field guessing. The full original binary frame
is retained by the caller. Schema source and SHA are recorded in README.md.
"""
import struct


def _varint(raw, pos):
    result = 0
    for shift in range(0, 70, 7):
        if pos >= len(raw):
            raise ValueError('truncated protobuf varint')
        value = raw[pos]; pos += 1
        result |= (value & 127) << shift
        if value < 128:
            if shift == 63 and value > 1:
                raise ValueError('protobuf varint overflow')
            return result, pos
    raise ValueError('protobuf varint overflow')


def fields(raw):
    pos = 0
    while pos < len(raw):
        key, pos = _varint(raw, pos)
        number, wire = key >> 3, key & 7
        if not number:
            raise ValueError('invalid protobuf field')
        if wire == 0:
            value, pos = _varint(raw, pos)
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            if pos + size > len(raw):
                raise ValueError('truncated protobuf fixed field')
            value = struct.unpack('<d' if wire == 1 else '<f', raw[pos:pos+size])[0]
            pos += size
        elif wire == 2:
            size, pos = _varint(raw, pos)
            if pos + size > len(raw):
                raise ValueError('truncated protobuf message')
            value = raw[pos:pos+size]; pos += size
        else:
            raise ValueError('unsupported protobuf wire type')
        yield number, wire, value


def _message(raw, expected):
    result = {}
    for n, wire, value in fields(raw):
        if n in expected:
            name, kind = expected[n]
            if wire != kind:
                raise ValueError('protobuf schema wire mismatch')
            if wire == 0 and value >= 2**63: value -= 2**64
            result[name] = value.decode('utf-8') if name == 'interval' else value
    return result


def decode(raw):
    if not isinstance(raw, bytes) or len(raw) > 8 * 1024 * 1024:
        raise ValueError('bounded binary protobuf frame required')
    response = dict(type=0, currentTs=0, feeds={}, segmentStatus={})
    for n, wire, value in fields(raw):
        if n in (1, 3):
            if wire != 0: raise ValueError('invalid response timestamp/type')
            if n == 3 and value >= 2**63: value -= 2**64
            response['type' if n == 1 else 'currentTs'] = value
        elif n == 2 and wire == 2:
            entry = dict((a, c) for a, b, c in fields(value))
            key = entry[1].decode('utf-8')
            if key in response['feeds']: raise ValueError('duplicate feed map key')
            feed = {}
            for a, b, c in fields(entry[2]):
                if a == 1 and b == 2:
                    feed['ltpc'] = _message(c, {1:('ltp',1), 2:('ltt',0)})
                elif a == 2 and b == 2:
                    for d, e, f in fields(c):
                        if d != 1 or e != 2: continue  # equities only
                        market = dict((g, i) for g, h, i in fields(f))
                        feed['ltpc'] = _message(market.get(1,b''), {1:('ltp',1),2:('ltt',0)})
                        feed['ohlc'] = [_message(i,{1:('interval',2),2:('open',1),3:('high',1),
                            4:('low',1),5:('close',1),6:('vol',0),7:('ts',0)})
                            for g,h,i in fields(market.get(4,b'')) if g==1 and h==2]
                        # vtt is cumulative today; never a per-minute volume.
            response['feeds'][key] = feed
        elif n == 4 and wire == 2:
            for a,b,c in fields(value):
                if a == 1 and b == 2:
                    entry = dict((d,f) for d,e,f in fields(c))
                    response['segmentStatus'][entry[1].decode()] = entry.get(2,0)
    return response
