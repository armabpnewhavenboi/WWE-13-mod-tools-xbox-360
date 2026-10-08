"""Just enough of the WMA (ASF) container to describe a song file.

Used by "x360music inspect" to compare our songs with ones the console
ripped itself.
"""

import struct
import uuid


def _guid(text):
    return uuid.UUID(text).bytes_le


HEADER = _guid("75B22630-668E-11CF-A6D9-00AA0062CE6C")
OBJECT_NAMES = {
    HEADER: "Header",
    _guid("75B22636-668E-11CF-A6D9-00AA0062CE6C"): "Data",
    _guid("33000890-E5B1-11CF-89F4-00A0C90349CB"): "Simple Index",
    _guid("D6E229D3-35DA-11D1-9034-00A0C90349BE"): "Index",
    _guid("8CABDCA1-A947-11CF-8EE4-00C00C205365"): "File Properties",
    _guid("B7DC0791-A9B7-11CF-8EE6-00C00C205365"): "Stream Properties",
    _guid("5FBF03B5-A92E-11CF-8EE3-00C00C205365"): "Header Extension",
    _guid("86D15240-311D-11D0-A3A4-00A0C90348F6"): "Codec List",
    _guid("75B22633-668E-11CF-A6D9-00AA0062CE6C"): "Content Description",
    _guid("D2D0A440-E307-11D2-97F0-00A0C95EA850"): "Extended Content Description",
    _guid("7BF875CE-468D-11D1-8D82-006097C9A2B2"): "Stream Bitrate Properties",
    _guid("2211B3FC-BD23-11D2-B4B7-00A0C955FC6E"): "Content Encryption",
    _guid("1EFB1A30-0B62-11D0-A39B-00A0C90348F6"): "Script Command",
}
FILE_PROPERTIES = _guid("8CABDCA1-A947-11CF-8EE4-00C00C205365")
STREAM_PROPERTIES = _guid("B7DC0791-A9B7-11CF-8EE6-00C00C205365")
CODEC_LIST = _guid("86D15240-311D-11D0-A3A4-00A0C90348F6")


class AsfError(Exception):
    pass


def _objects(data, start, end):
    pos = start
    while pos + 24 <= end:
        guid = bytes(data[pos:pos + 16])
        (size,) = struct.unpack_from("<Q", data, pos + 16)
        if size < 24 or pos + size > end:
            break
        yield guid, pos + 24, pos + size
        pos += size


def describe(data):
    """Dict of the interesting fields of an ASF file (bytes)."""
    if bytes(data[:16]) != HEADER:
        raise AsfError("not a WMA/ASF file")
    (header_size,) = struct.unpack_from("<Q", data, 16)
    info = {"objects": [], "header_objects": [], "codecs": []}
    for guid, start, end in _objects(data, 0, len(data)):
        info["objects"].append(OBJECT_NAMES.get(guid, str(uuid.UUID(bytes_le=guid))))
    for guid, start, end in _objects(data, 30, min(header_size, len(data))):
        info["header_objects"].append(OBJECT_NAMES.get(guid, str(uuid.UUID(bytes_le=guid))))
        if guid == FILE_PROPERTIES and end - start >= 80:
            (_, size, _, packets, play, send, preroll, flags,
             min_packet, max_packet, max_bitrate) = struct.unpack_from("<16sQQQQQQIIII", data, start)
            info.update(file_size=size, packets=packets, preroll_ms=preroll, flags=flags,
                        packet_size=min_packet if min_packet == max_packet else
                        "%d-%d" % (min_packet, max_packet),
                        max_bitrate=max_bitrate,
                        duration_ms=max(0, play // 10000 - preroll))
        elif guid == STREAM_PROPERTIES and end - start >= 72:
            (type_len,) = struct.unpack_from("<I", data, start + 40)
            fmt = start + 54
            if type_len >= 18:
                tag, channels, rate, avg, align, bits, extra = struct.unpack_from(
                    "<HHIIHHH", data, fmt)
                info.update(format_tag="0x%04X" % tag, channels=channels, sample_rate=rate,
                            bitrate=avg * 8, block_align=align, bits=bits,
                            codec_data=bytes(data[fmt + 18:fmt + 18 + extra]).hex())
        elif guid == CODEC_LIST:
            (count,) = struct.unpack_from("<I", data, start + 16)
            pos = start + 20
            for _ in range(count):
                try:
                    _, name_len = struct.unpack_from("<HH", data, pos)
                    name = bytes(data[pos + 4:pos + 4 + 2 * name_len]).decode("utf-16-le", "replace")
                    pos += 4 + 2 * name_len
                    (desc_len,) = struct.unpack_from("<H", data, pos)
                    desc = bytes(data[pos + 2:pos + 2 + 2 * desc_len]).decode("utf-16-le", "replace")
                    pos += 2 + 2 * desc_len
                    (info_len,) = struct.unpack_from("<H", data, pos)
                    pos += 2 + info_len
                except struct.error:
                    break
                info["codecs"].append(("%s %s" % (name.rstrip("\x00"), desc.rstrip("\x00"))).strip())
    return info
