"""Print the actual codec of each RTSP stream (main/sub) of the given cameras."""
import subprocess, sys, urllib.parse
from onvif_lib import Camera, NS, USER
for ip in sys.argv[1:]:
    c = Camera(ip); c.login(); m2 = c.media2()
    out = []
    for prof in ["MainStream", "SubStream"]:
        u = c.post(m2, '<GetStreamUri xmlns="%s"><Protocol>RTSP</Protocol><ProfileToken>%s</ProfileToken></GetStreamUri>' % (NS["tr2"], prof)).findtext("tr2:Uri", "", NS)
        auth = u.replace("rtsp://", f"rtsp://{USER}:{urllib.parse.quote(c.password, safe='')}@", 1)
        p = subprocess.run(["ffprobe", "-v", "error", "-rtsp_transport", "tcp", "-select_streams", "v:0", "-show_entries",
                            "stream=codec_name,width,height", "-of", "csv=p=0", auth], capture_output=True, text=True, timeout=40)
        out.append(f"{prof}={p.stdout.strip() or 'ERR ' + p.stderr.strip()[:120].replace(c.password, '***')}")
    print(ip, *out)
