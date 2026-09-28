from __future__ import annotations
import hashlib, struct
from pathlib import Path

def dds_fourcc(path):
    try:
        with Path(path).open("rb") as fh: head=fh.read(128)
        return head[84:88].decode("ascii",errors="ignore") if len(head)>=88 and head[:4]==b"DDS " else ""
    except OSError:return ""

def is_dxa5(path): return dds_fourcc(path)=="DXA5"

def decode_dxa5(path):
    """Decode There's DXA5 single-channel DDS as DXT5/BC4-style alpha blocks."""
    from PIL import Image
    data=Path(path).read_bytes()
    if len(data)<128 or data[:4]!=b"DDS " or data[84:88]!=b"DXA5": raise ValueError("Not a DXA5 DDS")
    height,width=struct.unpack_from("<II",data,12);src=memoryview(data)[128:];out=bytearray(width*height);off=0
    for by in range((height+3)//4):
        for bx in range((width+3)//4):
            if off+8>len(src): raise ValueError("Truncated DXA5 block data")
            a0,a1=int(src[off]),int(src[off+1]);bits=int.from_bytes(src[off+2:off+8],"little");off+=8
            if a0>a1: table=[a0,a1]+[((7-i)*a0+(i-1)*a1+3)//7 for i in range(2,8)]
            else: table=[a0,a1]+[((5-(i-2))*a0+(i-1)*a1+2)//5 for i in range(2,6)]+[0,255]
            for py in range(4):
                for px in range(4):
                    x=bx*4+px;y=by*4+py
                    if x<width and y<height: out[y*width+x]=table[(bits>>(3*(py*4+px)))&7]
    return Image.frombytes("L",(width,height),bytes(out))

def open_texture_image(path):
    from PIL import Image
    if is_dxa5(path): return decode_dxa5(path)
    img=Image.open(path);img.load();return img

def decoded_texture_path(path,cache_dir):
    p=Path(path)
    if not is_dxa5(p): return p
    cache=Path(cache_dir);cache.mkdir(parents=True,exist_ok=True);st=p.stat()
    key=hashlib.sha256(f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}|dxa5-v1".encode()).hexdigest()[:24]
    out=cache/f"{key}_dxa5.png"
    if not out.exists(): decode_dxa5(p).save(out,"PNG")
    return out


def blender_texture_path(path,cache_dir):
    """Return a Blender-safe PNG for There DDS textures, including DXA5."""
    p=Path(path)
    if not p.name.lower().endswith(".dds"): return p
    cache=Path(cache_dir);cache.mkdir(parents=True,exist_ok=True);st=p.stat()
    key=hashlib.sha256(f"{p.resolve()}|{st.st_size}|{st.st_mtime_ns}|blender-png-v1".encode()).hexdigest()[:24]
    out=cache/f"{key}_{p.stem}.png"
    if not out.exists():
        img=open_texture_image(p)
        try: img.save(out,"PNG")
        finally:
            try: img.close()
            except Exception: pass
    return out
