"""Read-only: log in, record model and current encoder configs (backup), check H.265 is offered."""
import concurrent.futures as cf, json, os, sys
import xml.etree.ElementTree as ET
from onvif_lib import Camera, NS, OnvifError

os.makedirs("backup", exist_ok=True)

def one(ip):
    cam = Camera(ip)
    try:
        info = cam.login()
        m2 = cam.media2()
        confs = cam.encoder_configs(m2)
        with open(f"backup/{ip}.xml", "wb") as f:
            f.write(b"<Backup>" + b"".join(ET.tostring(c) for c in confs) + b"</Backup>")
        streams = []
        for c in confs:
            tok = c.get("token")
            opts = cam.encoder_options(m2, tok)
            encs = sorted({o.findtext("tt:Encoding", "", NS) for o in opts})
            res = c.find("tt:Resolution", NS)
            streams.append({
                "token": tok, "enc": c.findtext("tt:Encoding", "", NS), "profile": c.get("Profile"),
                "res": f'{res.findtext("tt:Width", "", NS)}x{res.findtext("tt:Height", "", NS)}' if res is not None else "",
                "h265": "H265" in encs, "options": encs,
            })
        return {"ip": ip, "ok": True, "model": f'{info.get("Manufacturer")} {info.get("Model")}',
                "fw": info.get("FirmwareVersion"), "pw": cam.password and __import__("onvif_lib").PASSWORDS.index(cam.password),
                "media2": m2, "streams": streams}
    except Exception as e:
        return {"ip": ip, "ok": False, "error": f"{type(e).__name__}: {e}"[:200]}

# IPs as arguments, or one per line in cams.txt
ips = sys.argv[1:] or [l.split()[0] for l in open("cams.txt") if l.strip() and not l.startswith("#")]
with cf.ThreadPoolExecutor(16) as ex:
    res = sorted(ex.map(one, ips), key=lambda r: tuple(map(int, r["ip"].split("."))))
json.dump(res, open("inventory.json", "w"), indent=1, ensure_ascii=False)
