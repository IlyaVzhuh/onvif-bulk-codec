"""Minimal ONVIF SOAP client (WS-Security digest, Media2). Passwords to try: ~/.config/onvif-pass, one per line."""
import base64, datetime, hashlib, os, re, urllib.request
import xml.etree.ElementTree as ET

USER = os.environ.get("ONVIF_USER", "admin")
PASSWORDS = [l.strip() for l in open(os.path.expanduser("~/.config/onvif-pass")) if l.strip()]
NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "tr2": "http://www.onvif.org/ver20/media/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
}
for k, v in NS.items():
    ET.register_namespace(k, v)


class OnvifError(Exception):
    pass


def _security(password, offset):
    nonce = os.urandom(16)
    created = (datetime.datetime.now(datetime.timezone.utc) + offset).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()
    return (
        '<s:Header><Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-wssecurity-secext-1.0.xsd"><UsernameToken><Username>%s</Username>'
        '<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0'
        '#PasswordDigest">%s</Password><Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-soap-message-security-1.0#Base64Binary">%s</Nonce><Created xmlns="http://docs.'
        'oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">%s</Created>'
        "</UsernameToken></Security></s:Header>" % (USER, digest, base64.b64encode(nonce).decode(), created)
    )


class Camera:
    def __init__(self, ip):
        self.ip = ip
        self.device = f"http://{ip}/onvif/device_service"
        self.password = None
        self.offset = datetime.timedelta(0)

    def post(self, url, body, auth=True):
        header = _security(self.password, self.offset) if auth else ""
        env = f'<s:Envelope xmlns:s="{NS["s"]}">{header}<s:Body>{body}</s:Body></s:Envelope>'
        req = urllib.request.Request(url, env.encode(), {"Content-Type": "application/soap+xml; charset=utf-8"})
        try:
            data = urllib.request.urlopen(req, timeout=15).read()
        except urllib.error.HTTPError as e:
            data = e.read()
        root = ET.fromstring(data)
        fault = root.find(".//s:Fault", NS)
        if fault is not None:
            text = " ".join(t.strip() for t in fault.itertext() if t.strip())
            raise OnvifError(text[:300])
        return root.find("s:Body", NS)[0]

    def login(self):
        # Sync to the camera clock so digest timestamps are accepted even on skewed cameras.
        r = self.post(self.device, '<GetSystemDateAndTime xmlns="%s"/>' % NS["tds"], auth=False)
        u = r.find(".//tt:UTCDateTime", NS)
        if u is not None:
            g = lambda p: int(u.find(p, NS).text)
            cam = datetime.datetime(g("tt:Date/tt:Year"), g("tt:Date/tt:Month"), g("tt:Date/tt:Day"),
                                    g("tt:Time/tt:Hour"), g("tt:Time/tt:Minute"), g("tt:Time/tt:Second"),
                                    tzinfo=datetime.timezone.utc)
            # The camera reports whole seconds, so the raw offset can put Created in its future and
            # get rejected. Only follow its clock when it's really off, and stay a bit behind it.
            skew = cam - datetime.datetime.now(datetime.timezone.utc)
            if abs(skew) > datetime.timedelta(seconds=60):
                self.offset = skew - datetime.timedelta(seconds=5)
        last = None
        for pw in PASSWORDS:
            self.password = pw
            try:
                info = self.post(self.device, '<GetDeviceInformation xmlns="%s"/>' % NS["tds"])
                self.info = {c.tag.split("}")[1]: (c.text or "") for c in info}
                return self.info
            except OnvifError as e:
                last = e
        raise OnvifError(f"login failed: {last}")

    def media2(self):
        r = self.post(self.device, '<GetServices xmlns="%s"><IncludeCapability>false</IncludeCapability>'
                      "</GetServices>" % NS["tds"])
        for svc in r.findall("tds:Service", NS):
            if svc.findtext("tds:Namespace", "", NS) == NS["tr2"]:
                url = svc.findtext("tds:XAddr", "", NS)
                # Some cameras advertise their own LAN/other IP; keep the path, use the IP we reach.
                return re.sub(r"^http://[^/]+", f"http://{self.ip}", url)
        raise OnvifError("no Media2 service")

    def encoder_configs(self, m2):
        # The IPNC firmware faults on a bare GetVideoEncoderConfigurations; ask per token instead,
        # taking the tokens from the profiles.
        r = self.post(m2, '<GetProfiles xmlns="%s"><Type>VideoEncoder</Type></GetProfiles>' % NS["tr2"])
        tokens = list(dict.fromkeys(v.get("token") for v in r.iter(f'{{{NS["tr2"]}}}VideoEncoder')))
        confs = []
        for tok in tokens:
            r = self.post(m2, '<GetVideoEncoderConfigurations xmlns="%s"><ConfigurationToken>%s'
                          "</ConfigurationToken></GetVideoEncoderConfigurations>" % (NS["tr2"], tok))
            confs += r.findall("tr2:Configurations", NS)
        return confs

    def encoder_options(self, m2, token):
        r = self.post(m2, '<GetVideoEncoderConfigurationOptions xmlns="%s"><ConfigurationToken>%s'
                      "</ConfigurationToken></GetVideoEncoderConfigurationOptions>" % (NS["tr2"], token))
        return r.findall("tr2:Options", NS)
