"""Opt-in LAN pairing. No credentials, personal data or sessions persisted."""
import ipaddress,secrets,threading,time
from http.cookies import SimpleCookie,CookieError

class PairingError(ValueError):
    def __init__(self,message,status):super().__init__(message);self.status=status

def private_address(value):
    try:ip=ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:raise ValueError('手机地址必须是电脑的局域网IPv4地址。') from None
    if not any(ip in ipaddress.IPv4Network(network) for network in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')):raise ValueError('仅支持专用局域网地址，不支持公网地址。')
    return str(ip)

class LANAccess:
    def __init__(self,code=None,clock=time.monotonic):
        self.code=code or secrets.token_urlsafe(12);self.clock=clock;self.lock=threading.Lock();self.sessions={};self.attempts={}
    def authorized(self,cookie):
        try:parsed=SimpleCookie();parsed.load(cookie or '');session=parsed.get('shizhi_lan');value=session.value if session else ''
        except CookieError:return False
        with self.lock:return self.sessions.get(value,0)>self.clock()
    def login(self,ip,code):
        with self.lock:
            now=self.clock();self.attempts={key:value for key,value in self.attempts.items() if value[1]>now};self.sessions={key:value for key,value in self.sessions.items() if value>now}
            count,deadline=self.attempts.get(ip,(0,now+300))
            if count>=5:raise PairingError('尝试次数过多，请5分钟后重试。',429)
            if not isinstance(code,str) or len(code)>128 or not secrets.compare_digest(code.strip().encode(),self.code.encode()):
                if len(self.attempts)>=512 and ip not in self.attempts:raise PairingError('访问繁忙，请稍后重试。',429)
                self.attempts[ip]=(count+1,deadline);raise PairingError('访问码不正确，请查看电脑启动窗口。',401)
            if len(self.sessions)>=64:raise PairingError('已连接设备较多，请重启电脑端服务后重试。',429)
            session=secrets.token_urlsafe(32);self.sessions[session]=now+28800;self.attempts.pop(ip,None)
            return session
