import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import zlib

import pytest

from core.canonical import canonical_bytes, digest
from multimodal.codec import (MAX_MEDIA_BYTES, MEDIA_CONTEXT_CHARS, MEDIA_PREFIX,
    POLICY, MediaError, decode_request, encode_request, pack_media, read_selected,
    validate_media)


SIGNATURE = b"\x89PNG\r\n\x1a\n"


def chunk(kind, data=b""):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)


def ihdr(width=2, height=1, *, depth=8, color=2, compression=0, filtering=0, interlace=0):
    return chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color, compression, filtering, interlace))


def png(width=2, height=1, *, color=2, pixels=None, ancillary=()):
    if pixels is None:
        channels = 3 if color == 2 else 4
        pixels = (b"\0" + b"\x10\x20\x30\x40"[:channels] * width) * height
    return SIGNATURE + ihdr(width, height, color=color) + b"".join(ancillary) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND")


def riff(*chunks):
    data = b"WAVE" + b"".join(kind + struct.pack("<I", len(payload)) + payload for kind, payload in chunks)
    return b"RIFF" + struct.pack("<I", len(data)) + data


def wav(frames=160, *, code=1, channels=1, sample_rate=16000, byte_rate=32000,
        block_align=2, bits=16):
    fmt = struct.pack("<HHIIHH", code, channels, sample_rate, byte_rate, block_align, bits)
    return riff((b"fmt ", fmt), (b"data", b"\x00\x10" * frames))


def envelope(value):
    return MEDIA_PREFIX + canonical_bytes(value).decode("utf-8")


@pytest.mark.parametrize("raw,name,kind,mime,metadata", [
    (png(), "صورة.png", "image", "image/png", {"width":2,"height":1}),
    (png(color=6), "شفافية.png", "image", "image/png", {"width":2,"height":1}),
    (wav(), "صوت.wav", "audio", "audio/wav", {"sample_rate":16000,"channels":1,"sample_width":2,"frames":160}),
])
def test_pack_and_deep_validate_preserve_exact_bytes(raw, name, kind, mime, metadata):
    document = pack_media(raw, name)
    assert document == {"name":name,"kind":kind,"mime":mime,"metadata":metadata,
        "size_bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),
        "data_base64":base64.b64encode(raw).decode("ascii")}
    assert validate_media(document) == document
    assert validate_media(document) is not document
    assert validate_media(document)["metadata"] is not document["metadata"]


def test_maximum_valid_dimensions_duration_and_split_idat():
    assert pack_media(png(1000,1000), "million.png")["metadata"] == {"width":1000,"height":1000}
    assert pack_media(png(1024,1), "wide.png")["metadata"]["width"] == 1024
    assert pack_media(wav(128000), "eight.wav")["metadata"]["frames"] == 128000
    compressed = zlib.compress(b"\x04\0\0\0\0\0\0")
    raw = SIGNATURE + ihdr() + chunk(b"IDAT", compressed[:3]) + chunk(b"IDAT") + chunk(b"IDAT",compressed[3:]) + chunk(b"IEND")
    assert pack_media(raw,"split.png")["size_bytes"] == len(raw)


@pytest.mark.parametrize("filter_byte", range(5))
def test_all_png_filter_codes_are_permitted(filter_byte):
    assert pack_media(png(pixels=bytes([filter_byte])+b"\0"*6), "filter.png")["kind"] == "image"


def test_safe_ancillary_and_exact_extension_independence():
    raw = png(ancillary=(chunk(b"sRGB",b"\x01"),chunk(b"gAMA",struct.pack(">I",45455)),
                         chunk(b"pHYs",struct.pack(">IIB",3779,3779,1))))
    assert pack_media(raw,"اسم مختار.data")["mime"] == "image/png"


@pytest.mark.parametrize("name", ["", " ", ".hidden.png", ".", "..", "a/b.png", "a\\b.png",
    "a\x00.png", "a\n.png", "a\u200f.png", "a\ud800.png", "ع"*91, "x"*181, None, Path("p.png")])
def test_names_have_one_safe_unambiguous_basename(name):
    with pytest.raises(MediaError, match="media_name_invalid"):
        pack_media(png(), name)


@pytest.mark.parametrize("raw", [b"", b"x" * (MAX_MEDIA_BYTES+1)])
def test_byte_limit_is_enforced_before_decode(raw):
    with pytest.raises(MediaError, match="media_too_large"):
        pack_media(raw,"x.png")


@pytest.mark.parametrize("raw", [bytearray(png()), memoryview(png()), "not bytes", None])
def test_raw_bytes_are_explicit(raw):
    with pytest.raises(MediaError, match="media_invalid"):
        pack_media(raw,"x.png")


@pytest.mark.parametrize("raw", [b"<svg><script/></svg>", b"\xff\xd8jpeg", b"GIF89a", b"%PDF-1.0", b"ID3mp3"])
def test_unimplemented_formats_are_named_refusals(raw):
    with pytest.raises(MediaError, match="media_type_unsupported"):
        pack_media(raw,"x.data")


@pytest.mark.parametrize("header", [ihdr(0,1),ihdr(1,0),ihdr(1025,1),ihdr(1,1025),ihdr(1024,1024),
    ihdr(depth=16),ihdr(depth=1),ihdr(color=0),ihdr(color=3),ihdr(color=4),
    ihdr(compression=1),ihdr(filtering=1),ihdr(interlace=1)])
def test_png_header_contract(header):
    raw = SIGNATURE + header + chunk(b"IDAT", zlib.compress(b"\0"*7)) + chunk(b"IEND")
    with pytest.raises(MediaError, match="png_invalid"):
        pack_media(raw,"x.png")


@pytest.mark.parametrize("extra", [chunk(b"PLTE"),chunk(b"tEXt",b"ignored instruction"),chunk(b"acTL",b"\0"*8),
    chunk(b"fcTL",b"\0"*26),chunk(b"fdAT",b"\0"*4),chunk(b"ABCD"),chunk(b"bKGD",b"\0"*6),
    chunk(b"sRGB",b"\x04"),chunk(b"sRGB"),chunk(b"gAMA",b"\0"*4),chunk(b"gAMA",b"\0"*3),
    chunk(b"pHYs",struct.pack(">IIB",0,1,1)),chunk(b"pHYs",struct.pack(">IIB",1,1,2)),
    chunk(b"pHYs",b"\0"*8),chunk(b"sRGB",b"\0")+chunk(b"sRGB",b"\0"),
    chunk(b"sRGB",b"\0")+chunk(b"gAMA",struct.pack(">I",100000))])
def test_png_ancillary_whitelist_and_ambiguity(extra):
    with pytest.raises(MediaError, match="png_invalid"):
        pack_media(png(ancillary=(extra,)),"x.png")


@pytest.mark.parametrize("raw", [
    SIGNATURE + chunk(b"IDAT",b"x") + ihdr() + chunk(b"IEND"),
    SIGNATURE + ihdr() + ihdr() + chunk(b"IDAT",zlib.compress(b"\0"*7)) + chunk(b"IEND"),
    SIGNATURE + ihdr() + chunk(b"IEND"),
    SIGNATURE + ihdr() + chunk(b"IDAT",zlib.compress(b"\0"*7)) + chunk(b"sRGB",b"\0") + chunk(b"IEND"),
    png() + b"trailing", png() + chunk(b"IEND"), png()[:-12],
    SIGNATURE + ihdr() + chunk(b"IDAT",zlib.compress(b"\0"*7)) + chunk(b"IEND",b"x"),
    SIGNATURE + struct.pack(">I",0xffffffff) + b"IHDR" + b"\0"*17,
    png()[:20], png()[:-1],
])
def test_png_chunk_order_lengths_and_end(raw):
    with pytest.raises(MediaError, match="png_invalid"):
        pack_media(raw,"x.png")


def test_png_crc_is_checked_for_every_chunk():
    raw = bytearray(png(ancillary=(chunk(b"sRGB",b"\0"),)))
    offset = 8
    while offset < len(raw):
        size = struct.unpack_from(">I",raw,offset)[0]
        changed = bytearray(raw)
        changed[offset+8+size] ^= 1
        with pytest.raises(MediaError, match="png_invalid"):
            pack_media(bytes(changed),"x.png")
        offset += size+12


@pytest.mark.parametrize("compressed", [b"", b"not-zlib",zlib.compress(b"\0"*6),zlib.compress(b"\0"*8),
    zlib.compress(b"\x05"+b"\0"*6),zlib.compress(b"\0"*7)[:-1],
    zlib.compress(b"\0"*7)+b"extra",zlib.compress(b"\0"*7)+zlib.compress(b"\0"*7),
    zlib.compress(b"\0"*8_000_000)])
def test_png_bounded_inflate_exact_rows_and_single_stream(compressed):
    raw = SIGNATURE + ihdr() + chunk(b"IDAT",compressed) + chunk(b"IEND")
    with pytest.raises(MediaError, match="png_invalid"):
        pack_media(raw,"x.png")


@pytest.mark.parametrize("kwargs", [{"frames":0},{"frames":128001},{"code":3},{"channels":2},
    {"sample_rate":8000},{"byte_rate":16000},{"block_align":4},{"bits":8},{"bits":24}])
def test_wav_pcm_contract(kwargs):
    with pytest.raises(MediaError, match="wav_invalid"):
        pack_media(wav(**kwargs),"x.wav")


def test_wav_order_unknown_chunks_truncation_and_padding():
    fmt = struct.pack("<HHIIHH",1,1,16000,32000,2,16)
    bad = [riff((b"data",b"\0\0"),(b"fmt ",fmt)), riff((b"fmt ",fmt),(b"data",b"\0")),
           riff((b"fmt ",fmt+b"\0\0"),(b"data",b"\0\0")),
           riff((b"fmt ",fmt),(b"fmt ",fmt),(b"data",b"\0\0")),
           riff((b"fmt ",fmt),(b"data",b"\0\0"),(b"data",b"\0\0")),
           riff((b"fmt ",fmt),(b"JUNK",b"\0\0"),(b"data",b"\0\0")),
           wav()+b"trailer",wav()[:-1],wav()[:42]]
    raw = bytearray(wav())
    struct.pack_into("<I",raw,40,3)  # data has an odd declared size, regardless of pad byte.
    bad.append(bytes(raw))
    for raw in bad:
        with pytest.raises(MediaError, match="wav_invalid"):
            pack_media(raw,"x.wav")


@pytest.mark.parametrize("field,value", [("sha256","0"*64),("sha256",None),("size_bytes",0),
    ("size_bytes",True),("kind","audio"),("mime","text/plain"),("metadata",{"width":True,"height":1}),
    ("metadata",{"width":2,"height":1,"extra":0}),("metadata",{"width":2.0,"height":1})])
def test_document_claims_are_recomputed(field,value):
    document = pack_media(png(),"x.png")
    document[field] = value
    with pytest.raises(MediaError):
        validate_media(document)


@pytest.mark.parametrize("transform", [lambda d: {**d,"extra":0},lambda d: {k:v for k,v in d.items() if k!="sha256"},
    lambda d: {**d,"data_base64":d["data_base64"]+"\n"},
    lambda d: {**d,"data_base64":"!"+d["data_base64"]},
    lambda d: {**d,"data_base64":"A"*(4*((MAX_MEDIA_BYTES+2)//3)+4)},
    lambda d: {**d,"data_base64":d["data_base64"].encode()},
    lambda d: {**d,"data_base64":"\ud800"},lambda d: None])
def test_closed_document_and_canonical_base64(transform):
    with pytest.raises(MediaError):
        validate_media(transform(pack_media(png(),"x.png")))


def test_nonzero_base64_pad_bits_are_refused():
    document = pack_media(wav(frames=1),"x.wav")  # 46 bytes -> two trailing padding bytes.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    data = document["data_base64"]
    assert data.endswith("==")
    document["data_base64"] = data[:-3]+alphabet[alphabet.index(data[-3])+1]+"=="
    assert base64.b64decode(document["data_base64"]) == wav(frames=1)
    with pytest.raises(MediaError,match="media_invalid"):
        validate_media(document)


def test_rehashed_broken_format_still_refused():
    document = pack_media(png(),"x.png")
    raw = bytearray(png())
    raw[-1] ^= 1
    document["sha256"] = hashlib.sha256(raw).hexdigest()
    document["data_base64"] = base64.b64encode(raw).decode()
    with pytest.raises(MediaError, match="png_invalid"):
        validate_media(document)


def test_envelope_roundtrip_freezes_media_preferences_and_policy():
    document = pack_media(png(),"صورة.png")
    prefbody = {"revision":1,"values":{"verbosity":"concise"}}
    preferences = {**prefbody,"sha256":digest(prefbody)}
    text = encode_request("صف هذه الصورة\u200f",(document,),preferences)
    expected = decode_request(text)
    document["metadata"]["width"] = 123
    preferences["values"]["verbosity"] = "detailed"
    assert decode_request(text) == expected
    assert expected["policy"] == dict(POLICY)
    assert expected["media"][0]["metadata"]["width"] == 2
    assert expected["preferences"]["values"]["verbosity"] == "concise"
    assert text == envelope(expected)
    with pytest.raises(TypeError):
        POLICY["num_ctx"] = 1
    assert decode_request(encode_request("تابع",()))["media"] == []


@pytest.mark.parametrize("prompt", ["", " \n", "x"*4001, "\ud800", None, 5])
def test_user_request_is_bounded_text(prompt):
    with pytest.raises(MediaError, match="user_request_invalid"):
        encode_request(prompt,())


@pytest.mark.parametrize("media", [[], None, (pack_media(png(),"one.png"),pack_media(wav(),"two.wav"))])
def test_one_explicit_media_tuple_per_turn(media):
    with pytest.raises(MediaError, match="media_limit"):
        encode_request("طلب",media)


@pytest.mark.parametrize("mutation", [lambda d:d.update(extra=True),lambda d:d.pop("policy"),
    lambda d:d.update(schema_version=True),lambda d:d.update(kind="workspace_request"),
    lambda d:d["policy"].update(num_ctx=8193),lambda d:d["policy"].update(provider_version=2),
    lambda d:d["policy"].update(media_envelope_version=2),lambda d:d["policy"].update(version=True),
    lambda d:d["policy"].update(extra=1),lambda d:d.update(preferences={}),
    lambda d:d.update(media={})])
def test_envelope_closed_schema_fixed_policy_and_preferences(mutation):
    value = decode_request(encode_request("طلب",()))
    mutation(value)
    with pytest.raises(MediaError):
        decode_request(envelope(value))


def test_noncanonical_and_ambiguous_envelope_representation():
    text = encode_request("أ",(pack_media(png(),"x.png"),))
    decoded = decode_request(text)
    bad = [text+"\n",MEDIA_PREFIX+json.dumps(decoded,ensure_ascii=False),
           MEDIA_PREFIX+json.dumps(decoded,sort_keys=True,separators=(",",":"),ensure_ascii=True),
           text.replace('"kind":"multimodal_request"','"kind":"wrong","kind":"multimodal_request"'),
           text.replace('"schema_version":1','"schema_version":1.0'),
           text.replace('"schema_version":1','"schema_version":NaN'),
           text.removeprefix(MEDIA_PREFIX),MEDIA_PREFIX+"null",MEDIA_PREFIX+"[1]",None,
           MEDIA_PREFIX+" "*MEDIA_CONTEXT_CHARS]
    for value in bad:
        with pytest.raises(MediaError):
            decode_request(value)


def test_read_selected_requires_existing_regular_single_link_file(tmp_path):
    root = tmp_path.resolve()
    path = root/"صورة.png"
    path.write_bytes(png())
    before = (path.stat().st_mtime_ns,path.read_bytes())
    assert read_selected(path) == pack_media(png(),path.name)
    assert (path.stat().st_mtime_ns,path.read_bytes()) == before
    with pytest.raises(MediaError,match="file_missing"):
        read_selected(root/"absent"/"x.png")
    assert not (root/"absent").exists()
    with pytest.raises(MediaError,match="media_path_invalid"):
        read_selected(str(path))
    hardlink = root/"hard.png"
    os.link(path,hardlink)
    for entry in (path,hardlink):
        with pytest.raises(MediaError,match="unsafe_path"):
            read_selected(entry)


def test_selected_path_rejects_symlink_at_leaf_or_any_parent(tmp_path):
    root = tmp_path.resolve()
    real = root/"real"
    real.mkdir()
    file = real/"x.png"
    file.write_bytes(png())
    leaf = root/"leaf.png"
    leaf.symlink_to(file)
    parent = root/"link"
    parent.symlink_to(real,target_is_directory=True)
    for path in (leaf,parent/"x.png"):
        with pytest.raises(MediaError,match="unsafe_path"):
            read_selected(path)
    with pytest.raises(MediaError,match="path_invalid"):
        read_selected(real/".."/"real"/"x.png")


def test_selected_directory_fifo_and_oversized_input_are_refused(tmp_path):
    root = tmp_path.resolve()
    directory = root/"directory.png"
    directory.mkdir()
    fifo = root/"pipe.wav"
    os.mkfifo(fifo)
    for path in (directory,fifo):
        with pytest.raises(MediaError,match="unsafe_path"):
            read_selected(path)
    large = root/"large.png"
    large.write_bytes(b"x"*(MAX_MEDIA_BYTES+1))
    with pytest.raises(MediaError,match="media_too_large"):
        read_selected(large)
