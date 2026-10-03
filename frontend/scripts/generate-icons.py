"""Dependency-free PNG app icons from the same simple compass mark as icon.svg."""
from pathlib import Path
import struct
import zlib

def inside(x,y,points):
    signs=[(x-a[0])*(b[1]-a[1])-(y-a[1])*(b[0]-a[0]) for a,b in zip(points,points[1:]+points[:1])]
    return all(v>=0 for v in signs) or all(v<=0 for v in signs)

def chunk(kind,data):
    return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data))

for size in (192,512):
    data=bytearray()
    for y in range(size):
        data.append(0)
        for x in range(size):
            px,py=x*192/size,y*192/size
            # Outer mark is two triangles, leaving the bottom notch.
            filled=inside(px,py,[(96,39),(149,150),(96,127)]) or inside(px,py,[(96,39),(96,127),(43,150)])
            cut=inside(px,py,[(96,72),(115,120),(96,109)])
            data.extend((238,243,223) if filled and not cut else (23,61,55))
    header=struct.pack('!2I5B',size,size,8,2,0,0,0)
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',header)+chunk(b'IDAT',zlib.compress(bytes(data)))+chunk(b'IEND',b'')
    (Path(__file__).resolve().parents[1]/'public'/f'icon-{size}.png').write_bytes(png)
