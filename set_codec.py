"""Switch every video encoder of the given cameras to the given codec (H265 by default), keeping
resolution, bitrate, FPS and GOP. Usage: set_codec.py [--codec H264] IP [IP...]"""
import argparse, copy, sys
import xml.etree.ElementTree as ET
from onvif_lib import Camera, NS

ap = argparse.ArgumentParser()
ap.add_argument("--codec", default="H265")
ap.add_argument("ips", nargs="+")
args = ap.parse_args()
PROFILE = {"H265": "Main", "H264": "Main"}

def change(ip):
    cam = Camera(ip)
    cam.login()
    m2 = cam.media2()
    for conf in cam.encoder_configs(m2):
        tok = conf.get("token")
        enc = conf.find("tt:Encoding", NS)
        if enc.text == args.codec:
            print(f"{ip} {tok}: already {args.codec}")
            continue
        new = copy.deepcopy(conf)
        new.tag = f'{{{NS["tr2"]}}}Configuration'
        new.find("tt:Encoding", NS).text = args.codec
        new.set("Profile", PROFILE[args.codec])
        body = '<SetVideoEncoderConfiguration xmlns="%s">%s</SetVideoEncoderConfiguration>' % (
            NS["tr2"], ET.tostring(new, encoding="unicode"))
        cam.post(m2, body)
        print(f"{ip} {tok}: {enc.text} -> {args.codec}")
    now = {c.get("token"): c.findtext("tt:Encoding", "", NS) for c in cam.encoder_configs(m2)}
    assert all(v == args.codec for v in now.values()), f"{ip}: after set: {now}"
    return now

failed = []
for ip in args.ips:
    try:
        print(ip, "verified:", change(ip))
    except Exception as e:
        failed.append(ip)
        print(ip, "FAILED:", f"{type(e).__name__}: {e}"[:300])
sys.exit(1 if failed else 0)
