"""
西南科技大学教务系统 - 微信扫码登录模块
使用终端二维码, 微信扫码完成CAS认证
"""
import re
import time
import requests
import qrcode
from io import BytesIO


class WeChatLogin:
    """微信扫码登录类"""

    CAS_BASE = "http://cas.swust.edu.cn/authserver"
    SERVICE_URL = "https://matrix.dean.swust.edu.cn/acadmicManager/index.cfm?event=evaluateOnline:DEFAULT_EVENT"

    # 微信OAuth接口
    WECHAT_QRCODE_BASE = "https://open.weixin.qq.com/connect/qrcode"
    WECHAT_POLL_BASE = "https://long.open.weixin.qq.com/connect/l/qrconnect"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        self.uuid = None
        self.logged_in = False
        self._ticket = None

    def _get_uuid(self):
        """获取微信登录UUID"""
        print("[*] 正在获取微信登录二维码...")

        # 先访问CAS登录页面建立session (重要!)
        print("[*] 先访问CAS登录页建立连接...")
        self.session.get(
            f"{self.CAS_BASE}/login",
            params={"service": self.SERVICE_URL},
        )

        # 构造requestUrl
        login_url = (
            f"{self.CAS_BASE}/login?"
            f"service={requests.utils.quote(self.SERVICE_URL, safe='')}"
        )

        resp = self.session.get(
            f"{self.CAS_BASE}/weChatLogin",
            params={"requestUrl": login_url},
            allow_redirects=False,
        )

        if resp.status_code not in (302, 301):
            print(f"[!] 获取微信登录入口失败, 状态码: {resp.status_code}")
            return False

        location = resp.headers["Location"]
        print(f"[+] 微信OAuth URL: {location[:80]}...")

        # 访问微信OAuth页面获取UUID
        resp2 = self.session.get(location)
        resp2.encoding = "utf-8"

        # 从页面提取UUID (多种匹配模式)
        uuid_match = None
        
        # 模式1: var fordevtool = "...uuid=XXXXX"
        uuid_match = re.search(r'uuid=([a-zA-Z0-9]+)', resp2.text)
        
        # 模式2: img src="/connect/qrcode/XXXXX"
        if not uuid_match:
            uuid_match = re.search(r'/connect/qrcode/([a-zA-Z0-9]+)', resp2.text)
        
        # 模式3: G=\"XXXXX\"
        if not uuid_match:
            uuid_match = re.search(r'G\s*=\s*"([a-zA-Z0-9]+)"', resp2.text)

        if uuid_match:
            self.uuid = uuid_match.group(1)
            print(f"[+] 获取到UUID: {self.uuid}")
            return True
        else:
            print("[!] 未能从页面提取UUID")
            return False

    def show_qrcode(self):
        """在终端显示微信登录二维码"""
        if not self.uuid:
            print("[!] 请先获取UUID")
            return False

        # 下载微信二维码图片
        qrcode_url = f"{self.WECHAT_QRCODE_BASE}/{self.uuid}"
        print(f"[*] 正在下载二维码: {qrcode_url}")

        resp = requests.get(
            qrcode_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
                "Referer": "https://open.weixin.qq.com/",
            },
        )

        if resp.status_code != 200:
            print(f"[!] 下载二维码失败, 状态码: {resp.status_code}")
            return False

        # 保存二维码图片到文件 (备用)
        img_path = "qrcode.png"
        with open(img_path, "wb") as f:
            f.write(resp.content)
        print(f"[*] 二维码已保存到: {img_path}")

        # 在终端显示二维码
        print("\n" + "=" * 50)
        print("  请使用微信扫描以下二维码登录")
        print("  (也可打开当前目录下的 qrcode.png 扫码)")
        print("=" * 50 + "\n")

        img = qrcode.QRCode()
        # 从图片数据中解码并重新生成终端二维码
        from PIL import Image
        pil_img = Image.open(BytesIO(resp.content))
        # 使用qrcode库直接在终端打印
        qr = qrcode.QRCode(border=2)
        qr.add_data(qrcode_url)
        qr.make(fit=True)
        qr.print_ascii()

        print("\n[*] 等待扫码中... (有效期约5分钟)")
        return True

    def _poll_status(self):
        """轮询微信扫码状态"""
        if not self.uuid:
            return None

        poll_url = f"{self.WECHAT_POLL_BASE}?uuid={self.uuid}"
        # 添加随机参数防止缓存
        poll_url += f"&_={int(time.time() * 1000)}"

        try:
            resp = self.session.get(
                poll_url,
                headers={
                    "Referer": "https://open.weixin.qq.com/",
                },
                timeout=30,
            )
            resp.encoding = "utf-8"

            # 解析JSONP响应: window.wx_errcode=XXX;window.wx_code=XXX;
            errcode_match = re.search(r"wx_errcode\s*=\s*(\d+)", resp.text)
            code_match = re.search(r"wx_code\s*=\s*['\"]?([^'\";]+)['\"]?", resp.text)

            if errcode_match:
                errcode = int(errcode_match.group(1))
                wx_code = code_match.group(1) if code_match else None
                return {"errcode": errcode, "wx_code": wx_code}
            else:
                return {"errcode": -1, "wx_code": None}

        except requests.RequestException as e:
            print(f"  [!] 轮询出错: {e}")
            return None

    def wait_for_scan(self, timeout=300, poll_interval=2):
        """等待用户扫码并授权

        Args:
            timeout: 超时时间(秒), 默认5分钟
            poll_interval: 轮询间隔(秒)

        Returns:
            bool: 是否登录成功
        """
        start_time = time.time()
        last_status = None

        while time.time() - start_time < timeout:
            result = self._poll_status()

            if result is None:
                time.sleep(poll_interval)
                continue

            errcode = result["errcode"]
            wx_code = result.get("wx_code")

            # 状态变化时打印
            if errcode != last_status:
                if errcode == 408:
                    print("  [*] 等待扫码...")
                elif errcode == 404:
                    print("  [+] 已扫描! 请在手机上确认登录...")
                elif errcode == 403:
                    print("  [!] 你已取消登录, 请重新扫码")
                    return False
                elif errcode == 402:
                    print("  [!] 二维码已过期, 请重新获取")
                    return False
                elif errcode == 405:
                    print("  [+] 扫码成功! 正在完成登录...")
                    if wx_code:
                        return self._complete_login(wx_code)
                    else:
                        print("  [!] 未获取到授权码")
                        return False
                elif errcode == 500:
                    print("  [!] 服务器错误, 正在重试...")
                else:
                    print(f"  [*] 状态码: {errcode}")

                last_status = errcode

            time.sleep(poll_interval)

        print(f"[!] 扫码超时 ({timeout}秒)")
        return False

    def _complete_login(self, wx_code):
        """完成登录: 用wx_code回调CAS"""
        callback_url = f"{self.CAS_BASE}/callback"
        params = {
            "code": wx_code,
            "state": "",
        }

        print(f"[*] 正在完成CAS认证...")
        print(f"    callback URL: {callback_url}")
        print(f"    code: {wx_code[:30]}...")

        resp = self.session.get(
            callback_url,
            params=params,
            allow_redirects=False,
        )

        print(f"[*] 回调响应状态码: {resp.status_code}")
        print(f"[*] 回调Location: {resp.headers.get('Location', 'N/A')}")

        # 检查是否获取到了TGC (CAS认证成功的标志)
        tgc = self.session.cookies.get("TGC", "")
        if tgc:
            print(f"[+] 获取到TGC认证凭证! CAS认证成功!")
            print(f"    TGC: {tgc[:50]}...")
        else:
            print("[!] 未获取到TGC, 认证可能失败")

        # 有TGC后, 直接访问教务系统, CAS会自动签发ticket
        services_to_try = [
            self.SERVICE_URL,
            "https://matrix.dean.swust.edu.cn/acadmicManager/index.cfm?event=studentPortal:DEFAULT_EVENT",
        ]

        for svc_url in services_to_try:
            print(f"\n[*] 尝试访问: {svc_url[:80]}...")

            test_resp = self.session.get(
                svc_url,
                allow_redirects=False,
            )
            print(f"    状态码: {test_resp.status_code}")

            loc = test_resp.headers.get("Location", "")
            print(f"    Location: {loc[:120] if loc else 'N/A'}")

            # 情况1: 直接200, 已进入
            if test_resp.status_code == 200:
                if "matrix.dean" in test_resp.url.lower():
                    print("[+] ====== 登录成功, 已进入教务系统! ======")
                    self.logged_in = True
                    return True

            # 情况2: 302重定向到带ticket的URL
            if test_resp.status_code in (302, 301):
                if loc and "ticket=" in loc:
                    print(f"[+] CAS已签发ticket, 跟随中...")
                    ticket_resp = self.session.get(loc, allow_redirects=True)
                    print(f"    最终URL: {ticket_resp.url}")
                    if "matrix.dean" in ticket_resp.url and "login" not in ticket_resp.url.lower():
                        print("[+] ====== 登录成功, 已进入教务系统! ======")
                        self.logged_in = True
                        return True

                # 情况3: 重定向到CAS, CAS会自动处理(因为有TGC)
                if loc and "cas.swust.edu.cn" in loc and "login" in loc:
                    print(f"[*] 重定向到CAS登录页, 因为有TGC应自动签发ticket...")
                    cas_resp = self.session.get(loc, allow_redirects=False)
                    cas_loc = cas_resp.headers.get("Location", "")
                    print(f"    CAS响应: {cas_resp.status_code}")
                    print(f"    CAS Location: {cas_loc[:120] if cas_loc else 'N/A'}")

                    if cas_loc and "ticket=" in cas_loc:
                        print(f"[+] 获取到ticket!")
                        final_resp = self.session.get(cas_loc, allow_redirects=True)
                        print(f"    最终URL: {final_resp.url}")
                        if "matrix.dean" in final_resp.url:
                            print("[+] ====== 登录成功, 已进入教务系统! ======")
                            self.logged_in = True
                            return True

                # 情况4: 跟随重定向看最终位置
                if loc:
                    follow = self.session.get(loc, allow_redirects=False)
                    follow_loc = follow.headers.get("Location", "")
                    # 最多跟随3次
                    for i in range(3):
                        if not follow_loc:
                            break
                        if "ticket=" in follow_loc:
                            ft = self.session.get(follow_loc, allow_redirects=True)
                            print(f"    最终URL: {ft.url}")
                            if "matrix.dean" in ft.url:
                                print("[+] ====== 登录成功! ======")
                                self.logged_in = True
                                return True
                            break
                        follow = self.session.get(follow_loc, allow_redirects=False)
                        follow_loc = follow.headers.get("Location", "")

        print("[!] 登录验证失败")
        return False

    def login(self):
        """执行完整微信扫码登录流程"""
        # Step 1: 获取UUID
        if not self._get_uuid():
            return False

        # Step 2: 显示二维码
        if not self.show_qrcode():
            return False

        # Step 3: 等待扫码
        return self.wait_for_scan()

    def get_session(self):
        """返回当前session"""
        return self.session

    def is_logged_in(self):
        """是否已登录"""
        return self.logged_in


def explore_portal(session):
    """探索教务系统页面结构"""
    from bs4 import BeautifulSoup
    import re

    STUDENT_PORTAL = "https://matrix.dean.swust.edu.cn/acadmicManager/index.cfm?event=studentPortal:DEFAULT_EVENT"

    print("\n" + "=" * 60)
    print("正在探索教务系统页面...")
    print("=" * 60)

    resp = session.get(STUDENT_PORTAL, allow_redirects=True)
    resp.encoding = "utf-8"
    print(f"状态码: {resp.status_code}")
    print(f"当前URL: {resp.url}")

    soup = BeautifulSoup(resp.text, "html.parser")
    print(f"页面标题: {soup.title.text.strip() if soup.title else 'N/A'}")

    # 1. 链接
    print("\n--- 页面链接 ---")
    links = soup.find_all("a", href=True)
    for link in links:
        href = link.get("href", "")
        text = link.get_text(strip=True)
        if text and len(text) > 1 and not href.startswith("javascript"):
            print(f"  [{text[:40]}]")
            print(f"    -> {href[:100]}")

    # 2. 表单
    print("\n--- 表单 ---")
    forms = soup.find_all("form")
    for form in forms:
        action = form.get("action", "no action")
        method = form.get("method", "GET")
        fid = form.get("id", "no id")
        name = form.get("name", "")
        print(f"  ID={fid} name={name} method={method}")
        for inp in form.find_all(["input", "select"]):
            n = inp.get("name", "")
            t = inp.get("type", "text")
            if n:
                print(f"    {n} ({t})")

    # 3. 文本内容
    print("\n--- 主要内容文本 ---")
    body = soup.find("body")
    if body:
        text = body.get_text(separator="\n", strip=True)
        for line in text.split("\n"):
            line = line.strip()
            if line and len(line) > 3 and len(line) < 120:
                print(f"  {line}")

    # 保存
    with open("portal_page.html", "w", encoding="utf-8") as f:
        f.write(resp.text)
    print("\n[+] 完整HTML已保存到 portal_page.html")


def main():
    """测试微信扫码登录"""
    login = WeChatLogin()

    if login.login():
        print("\n[+] 登录成功! session已就绪")
        print("[*] 正在探索教务系统...")
        explore_portal(login.get_session())
    else:
        print("\n[!] 登录失败")


if __name__ == "__main__":
    main()
