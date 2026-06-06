"""Quick GLB JSON-header scanner to confirm what nodes are baked in."""
import sys, struct, json
p = sys.argv[1]
with open(p, "rb") as f:
    magic = f.read(4); ver = struct.unpack("<I", f.read(4))[0]; total = struct.unpack("<I", f.read(4))[0]
    clen = struct.unpack("<I", f.read(4))[0]; ctype = f.read(4)
    chunk = f.read(clen).decode("utf-8", errors="replace")
doc = json.loads(chunk)
print("scene:", doc.get("scene"))
print("nodes:", [n.get("name") for n in doc.get("nodes", [])])
print("meshes:", [m.get("name") for m in doc.get("meshes", [])])
print("materials:", [m.get("name") for m in doc.get("materials", [])])
print("skins:", [s.get("name") for s in doc.get("skins", [])])
