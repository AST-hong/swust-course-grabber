"""
西南科技大学教务系统 - CAS短信登录模块
"""
import requests
import re
from bs4 import BeautifulSoup


class CASLogin:
    """CAS统一认证登录类"""

    # CAS认证服务器地址
    CAS_BASE = "http://cas.swust.edu.cn/authserver"
    LOGIN_URL = f"{CAS_BASE}/login"
    GET_DYNAMIC_CODE_URL = f"{CAS_BASE}/getDynamicCode"

    # 教务系统地址
    SERVICE_URL = "https://matrix.dean.swust.edu.cn/acadmicManager/index.cfm?event=evaluateOnline:DEFAULT_EVENT"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 10; SM-G9750) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/91.0.4472.164 Mobile Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        })
        self.execution = None
        self.logged_in = False

    def _get_login_page(self):
        """获取登录页面, 提取execution等隐藏参数"""
        print("[*] 正在获取登录页面...")
        resp = self.session.get(
            self.LOGIN_URL,
            params={"service": self.SERVICE_URL},
        )
        resp.encoding = "utf-8"

        if resp.status_code != 200:
            print(f"[!] 获取登录页面失败, 状态码: {resp.status_code}")
            return False

        # 提取 execution 值
        soup = BeautifulSoup(resp.text, "html.parser")
        execution_input = soup.find("input", {"name": "execution"})
        if execution_input:
            self.execution = execution_input.get("value", "")
            print(f"[+] 获取到 execution: {self.execution}")
        else:
            # 尝试从多个form中查找 (包含短信登录form)
            forms = soup.find_all("input", {"name": "execution"})
            if forms:
                self.execution = forms[0].get("value", "")
                print(f"[+] 获取到 execution: {self.execution}")
            else:
                print("[!] 未能提取 execution 值")
                # 尝试用正则匹配
                match = re.search(
                    r'name="execution"\s+value="([^"]*)"', resp.text
                )
                if match:
                    self.execution = match.group(1)
                    print(f"[+] 正则匹配到 execution: {self.execution}")
                else:
                    print("[!] 未能从页面提取 execution, 尝试使用默认值")
                    self.execution = "e1s1"

        print(f"[+] Cookies: {dict(self.session.cookies)}")
        return True

    def send_sms_code(self, phone_number):
        """发送短信验证码, 发送成功后自动更新execution"""
        print(f"[*] 正在向 {phone_number} 发送验证码...")

        # 添加 AJAX 请求头
        self.session.headers["X-Requested-With"] = "XMLHttpRequest"
        self.session.headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        self.session.headers["Referer"] = (
            f"{self.LOGIN_URL}?service={requests.utils.quote(self.SERVICE_URL, safe='')}"
        )

        resp = self.session.post(
            self.GET_DYNAMIC_CODE_URL,
            data={"mobile": phone_number},
        )
        resp.encoding = "utf-8"

        # 恢复普通请求头
        del self.session.headers["X-Requested-With"]
        self.session.headers["Content-Type"] = "application/x-www-form-urlencoded"

        print(f"[*] 状态码: {resp.status_code}")

        try:
            result = resp.json()
            print(f"[+] 服务器响应: {result}")

            if result.get("success"):
                print(f"[+] 验证码发送成功! {result.get('msg', '')}")
                # ⚠️ 关键: 发送验证码后 flow 状态会推进, 需要重新获取 execution
                print("[*] 正在更新登录状态...")
                if not self._get_login_page():
                    print("[!] 更新登录状态失败")
                    return False
                return True
            else:
                msg = result.get('msg', '未知错误')
                print(f"[!] 验证码发送失败: {msg}")
                return False
        except Exception as e:
            print(f"[!] 解析响应失败: {e}")
            print(f"[!] 原始响应: {resp.text[:500]}")
            return False

    def login_with_sms(self, phone_number, sms_code):
        """使用短信验证码登录"""
        if not self.execution:
            print("[!] 请先获取登录页面")
            return False

        print(f"[*] 正在使用验证码登录...")
        print(f"   手机号: {phone_number}")
        print(f"   验证码: {sms_code}")
        print(f"   execution: {self.execution}")

        # 构建登录表单数据
        login_data = {
            "username": phone_number,
            "dynamicCode": sms_code,
            "execution": self.execution,
            "_eventId": "submit",
            "geolocation": "",
            "lm": "dynamicLogin",
        }

        # 设置表单提交请求头
        self.session.headers["Referer"] = (
            f"{self.LOGIN_URL}?service={requests.utils.quote(self.SERVICE_URL, safe='')}"
        )
        self.session.headers["Origin"] = "http://cas.swust.edu.cn"

        # 提交登录 (CAS登录会返回302重定向)
        resp = self.session.post(
            self.LOGIN_URL,
            data=login_data,
            params={"service": self.SERVICE_URL},
            allow_redirects=False,  # 手动处理重定向
        )

        print(f"[*] 登录响应状态码: {resp.status_code}")
        print(f"[*] 响应头Location: {resp.headers.get('Location', '无')}")

        # CAS认证逻辑：成功会302重定向到service+ticket
        if resp.status_code in (302, 301):
            redirect_url = resp.headers.get("Location", "")
            print(f"[+] 重定向到: {redirect_url[:200]}...")

            if "ticket=" in redirect_url:
                print("[+] 登录成功! CAS已签发ticket")
                # 跟随重定向到教务系统
                final_resp = self.session.get(
                    redirect_url,
                    allow_redirects=True,
                )
                final_resp.encoding = "utf-8"
                print(f"[+] 教务系统响应状态码: {final_resp.status_code}")
                print(f"[+] 最终URL: {final_resp.url}")

                # 检查是否成功进入教务系统
                if "login" not in final_resp.url.lower():
                    print("[+] 已成功进入教务系统!")
                    self.logged_in = True
                    return True
                else:
                    print("[!] 似乎跳转回了登录页, 登录可能未完全成功")
                    return False
            elif "error" in redirect_url.lower():
                print(f"[!] CAS返回错误: {redirect_url[:200]}")
                return False
            else:
                print(f"[!] 重定向URL中没有ticket, 登录可能失败")
                return False

        # 状态码200，还在登录页，说明登录失败
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        # 查找错误提示
        error_elem = soup.find("p", class_="textError")
        if error_elem:
            print(f"[!] 登录失败: {error_elem.text.strip()}")
        else:
            # 尝试查找其他错误提示
            for p in soup.find_all("p"):
                text = p.text.strip()
                if text and ("错误" in text or "失败" in text):
                    print(f"[!] 登录失败: {text}")
                    break
            else:
                print("[!] 登录失败, 请检查手机号和验证码是否正确")

        return False

    def get_session(self):
        """返回当前session供后续使用"""
        return self.session

    def is_logged_in(self):
        """检查是否已登录"""
        return self.logged_in


def main():
    """测试登录流程"""
    login = CASLogin()

    # 第一步：获取登录页
    if not login._get_login_page():
        print("[!] 无法获取登录页面, 退出")
        return

    # 第二步：获取用户手机号
    phone = input("请输入手机号: ").strip()
    if not phone:
        print("[!] 手机号不能为空")
        return

    # 第三步：发送验证码
    if not login.send_sms_code(phone):
        print("[!] 发送验证码失败")
        return

    # 第四步：输入验证码并登录
    sms_code = input("请输入收到的验证码: ").strip()
    if not sms_code:
        print("[!] 验证码不能为空")
        return

    if login.login_with_sms(phone, sms_code):
        print("\n[+] ====== 登录成功! ======")
        # 后续可以使用 login.get_session() 来进行选课操作
    else:
        print("\n[!] ====== 登录失败! ======")


if __name__ == "__main__":
    main()
