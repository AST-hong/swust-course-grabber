"""
西南科技大学教务系统 - 抢课主脚本
功能:
  1. 微信扫码登录 (Cookie持久化, 免重复登录)
  2. 定时启动 + 自动重试
  3. 选课逻辑 (待明天系统开放后完善)
"""
import os
import re
import sys
import json
import time
import signal
import pickle
import threading
from datetime import datetime, timedelta

# 全局退出标志
_exit_flag = False

def _signal_handler(signum, frame):
    """Ctrl+C 信号处理"""
    global _exit_flag
    _exit_flag = True
    print("\n\n[!] 收到退出信号 (Ctrl+C), 正在安全退出...")
    sys.exit(0)
from bs4 import BeautifulSoup
import requests
from course_parser import ScheduleParser

# ============================================================
# 配置区
# ============================================================
CONFIG = {
    # 教务系统URL
    "cas_base": "http://cas.swust.edu.cn/authserver",
    "service_url": "https://matrix.dean.swust.edu.cn/acadmicManager/index.cfm?event=studentPortal:DEFAULT_EVENT",
    "choose_course_url": "https://matrix.dean.swust.edu.cn/acadmicManager/index.cfm?event=chooseCourse:DEFAULT_EVENT",
    "course_table_url": "https://matrix.dean.swust.edu.cn/acadmicManager/index.cfm?event=chooseCourse:courseTable",

    # 抢课时间 (初选: 2026-06-24 09:00)
    "start_time": datetime(2026, 6, 25, 9, 0, 0),

    # 提前多久开始准备 (秒)
    "prepare_advance": 300,

    # 重试配置
    "max_retries": 50,
    "retry_interval": 0.1,  # 秒

    # 选课学期参数 (CT值，当前为2)
    "ct": "2",

    # Cookie文件
    "cookie_file": "cookies.pkl",

    # 选课关键词 (优先读取 targets.json，否则使用此处配置)
    "target_courses": [
        # {"keyword": "大数据", "teacher": "刘孟琴", "priority": 1,
        #  "time": "星期二第三讲", "weeks": "01-16", "place": "西7"},
    ],
    # 目标配置文件
    "targets_file": "targets.json",
}


# ============================================================
# 工具函数
# ============================================================
def save_cookies(session, filepath):
    """保存session cookies到文件"""
    with open(filepath, "wb") as f:
        pickle.dump(session.cookies, f)
    print(f"[+] Cookies已保存到 {filepath}")


def load_cookies(session, filepath):
    """从文件加载cookies到session"""
    if os.path.exists(filepath):
        with open(filepath, "rb") as f:
            session.cookies.update(pickle.load(f))
        return True
    return False


def load_targets(filepath="targets.json"):
    """从 JSON 文件加载目标课程列表"""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            targets = json.load(f)
            if isinstance(targets, list):
                return targets
    return None


def save_targets(targets, filepath="targets.json"):
    """保存目标课程列表到 JSON 文件"""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(targets, f, ensure_ascii=False, indent=2)
    print(f"[+] 目标课程已保存到 {filepath}")


def interactive_setup(filepath="targets.json"):
    """交互式配置目标课程"""
    print("\n" + "=" * 50)
    print("  抢课目标设置")
    print("=" * 50)
    print()

    # 加载已有配置
    existing = load_targets(filepath) or []
    if existing:
        print(f"当前已配置 {len(existing)} 门课程:")
        for i, t in enumerate(existing, 1):
            extras = []
            if t.get("teacher"):
                extras.append(f"教师:{t['teacher']}")
            if t.get("time"):
                extras.append(t["time"])
            if t.get("weeks"):
                extras.append(f"{t['weeks']}周")
            if t.get("place"):
                extras.append(t["place"])
            extra_str = "  ".join(extras) if extras else "不限条件"
            print(f"  {i}. {t.get('keyword','?')}  [{t.get('priority',1)}] {extra_str}")
        print()
        choice = input("是否覆盖? (y=重新配置 / n=保留并退出 / a=追加): ").strip().lower()
        if choice == "n":
            print("[*] 保持现有配置")
            return existing
        elif choice == "a":
            pass  # 在现有基础上追加
        else:
            existing = []

    targets = existing.copy()

    # 课程分类选项
    from main import CourseSelector
    cat_options = [c[3] for c in CourseSelector.CATEGORIES]  # label在第4位

    while True:
        try:
            n = int(input("请输入目标课程数量: ").strip())
            if n >= 0:
                break
        except ValueError:
            pass
        print("  请输入有效数字")

    if n == 0:
        print("[*] 清空所有目标课程")
        save_targets([], filepath)
        return []

    start_idx = len(targets)
    for i in range(n):
        num = start_idx + i + 1
        print(f"\n--- 课程 {num} ---")
        keyword = input("  关键词 (如: 机器学习) [必填]: ").strip()
        if not keyword:
            print("  关键词不能为空, 跳过")
            continue
        teacher = input("  教师 (回车=不限): ").strip()
        time_str = input("  上课时间 (如: 星期二第三讲, 回车=不限): ").strip()
        weeks = input("  上课周次 (如: 01-16, 回车=不限): ").strip()
        place = input("  上课地点 (如: 西71302, 回车=不限): ").strip()

        # 课程分类
        print(f"  课程分类 (0=所有分类):")
        for idx, cat in enumerate(cat_options, 1):
            print(f"    {idx}. {cat}")
        cat_sel = input(f"  选择 (1-{len(cat_options)}, 回车=所有): ").strip()
        categories = []
        if cat_sel:
            try:
                ci = int(cat_sel)
                if 1 <= ci <= len(cat_options):
                    categories = [cat_options[ci-1]]
            except ValueError:
                pass

        while True:
            try:
                pri = input("  优先级 (1最高, 回车=1): ").strip()
                priority = int(pri) if pri else 1
                if priority >= 1:
                    break
            except ValueError:
                pass
            print("  请输入正整数")

        targets.append({
            "keyword": keyword,
            "teacher": teacher,
            "priority": priority,
            "time": time_str,
            "weeks": weeks,
            "place": place,
            "categories": categories,
        })

    # 按优先级排序
    targets.sort(key=lambda x: x.get("priority", 99))

    print(f"\n[+] 配置完成, 共 {len(targets)} 门目标课程:")
    for i, t in enumerate(targets, 1):
        parts = []
        if t.get("teacher"):
            parts.append(f"教师:{t['teacher']}")
        if t.get("categories"):
            parts.append(f"[{','.join(t['categories'])}]")
        if t.get("time"):
            parts.append(t["time"])
        if t.get("weeks"):
            parts.append(f"{t['weeks']}周")
        if t.get("place"):
            parts.append(t["place"])
        info = "  ".join(parts) if parts else "不限"
        print(f"  {i}. [{t['priority']}] {t['keyword']} ({info})")

    save_targets(targets, filepath)
    return targets


# ============================================================
# 登录模块
# ============================================================
class LoginManager:
    """登录管理器 (支持Cookie持久化)"""

    WECHAT_QRCODE_BASE = "https://open.weixin.qq.com/connect/qrcode"
    WECHAT_POLL_BASE = "https://long.open.weixin.qq.com/connect/l/qrconnect"

    def __init__(self, config):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        })
        self.uuid = None
        self.logged_in = False

    def try_restore_session(self):
        """尝试从文件恢复session"""
        if load_cookies(self.session, self.config["cookie_file"]):
            # 有cookie, 测试是否还有效
            print("[*] 发现已保存的登录凭证, 验证中...")
            if self._test_session():
                print("[+] 登录凭证有效, 无需重新登录!")
                self.logged_in = True
                return True
            else:
                print("[!] 登录凭证已过期, 需要重新登录")
        return False

    def _test_session(self):
        """测试当前session是否有效"""
        try:
            resp = self.session.get(
                self.config["choose_course_url"],
                allow_redirects=False,
                timeout=10,
            )
            # 如果直接200且不在登录页, 说明session有效
            if resp.status_code == 200:
                return "login" not in resp.url.lower()
            # 如果302到ticket, 表示CAS需要重新签发(但session有效)
            if resp.status_code in (302, 301):
                loc = resp.headers.get("Location", "")
                if "ticket=" in loc or "matrix.dean" in loc:
                    return True
        except:
            pass
        return False

    def login(self):
        """执行微信扫码登录"""
        # 先尝试恢复
        if self.try_restore_session():
            return True

        print("\n" + "=" * 50)
        print("  需要微信扫码登录")
        print("=" * 50)

        # Step 1: 访问CAS登录页建立session
        self.session.get(
            f"{self.config['cas_base']}/login",
            params={"service": self.config["service_url"]},
        )

        # Step 2: 获取UUID
        login_url = (
            f"{self.config['cas_base']}/login?"
            f"service={requests.utils.quote(self.config['service_url'], safe='')}"
        )
        resp = self.session.get(
            f"{self.config['cas_base']}/weChatLogin",
            params={"requestUrl": login_url},
            allow_redirects=False,
        )
        if resp.status_code not in (302, 301):
            print("[!] 获取微信登录入口失败")
            return False

        resp2 = self.session.get(resp.headers["Location"])
        resp2.encoding = "utf-8"
        uuid_match = re.search(r'uuid=([a-zA-Z0-9]+)', resp2.text)
        if not uuid_match:
            uuid_match = re.search(r'/connect/qrcode/([a-zA-Z0-9]+)', resp2.text)
        if not uuid_match:
            print("[!] 无法提取UUID")
            return False
        self.uuid = uuid_match.group(1)

        # Step 3: 显示终端二维码
        self._display_qrcode()

        # Step 4: 等待扫码
        if not self._wait_for_scan():
            return False

        # Step 5: 完成认证
        if not self._complete_auth():
            return False

        # 保存cookies
        save_cookies(self.session, self.config["cookie_file"])
        self.logged_in = True
        return True

    def _display_qrcode(self):
        """显示终端二维码"""
        import qrcode
        from PIL import Image
        from io import BytesIO

        qr_url = f"{self.WECHAT_QRCODE_BASE}/{self.uuid}"
        print(f"\n[*] 二维码URL: {qr_url}")

        # 下载并显示
        img_resp = requests.get(qr_url, headers={"Referer": "https://open.weixin.qq.com/"})
        img = Image.open(BytesIO(img_resp.content))
        img.save("qrcode.png")
        print("[*] 二维码已保存到 qrcode.png")

        qr = qrcode.QRCode(border=2)
        qr.add_data(qr_url)
        qr.make(fit=True)
        qr.print_ascii()
        print("\n[*] 请使用微信扫码...")

    def _wait_for_scan(self, timeout=300):
        """等待扫码"""
        start = time.time()
        last_status = None

        while time.time() - start < timeout:
            try:
                poll_url = (
                    f"{self.WECHAT_POLL_BASE}?uuid={self.uuid}"
                    f"&_={int(time.time() * 1000)}"
                )
                resp = self.session.get(
                    poll_url,
                    headers={"Referer": "https://open.weixin.qq.com/"},
                    timeout=30,
                )
                resp.encoding = "utf-8"

                errcode_m = re.search(r"wx_errcode\s*=\s*(\d+)", resp.text)
                code_m = re.search(r"wx_code\s*=\s*['\"]?([^'\";]+)['\"]?", resp.text)
                errcode = int(errcode_m.group(1)) if errcode_m else -1
                wx_code = code_m.group(1) if code_m else None

                if errcode != last_status:
                    if errcode == 408:
                        print("  [*] 等待扫码...")
                    elif errcode == 404:
                        print("  [+] 已扫描! 请确认登录...")
                    elif errcode == 403:
                        print("  [!] 取消登录")
                        return False
                    elif errcode == 402:
                        print("  [!] 二维码过期")
                        return False
                    elif errcode == 405:
                        print("  [+] 扫码成功!")
                        self._wx_code = wx_code
                        return True
                    last_status = errcode

                time.sleep(2)
            except Exception as e:
                print(f"  [!] 轮询出错: {e}")
                time.sleep(2)

        print("[!] 扫码超时")
        return False

    def _complete_auth(self):
        """完成CAS认证"""
        resp = self.session.get(
            f"{self.config['cas_base']}/callback",
            params={"code": self._wx_code, "state": ""},
            allow_redirects=False,
        )

        tgc = self.session.cookies.get("TGC", "")
        if not tgc:
            print("[!] 未获取TGC")
            return False

        print(f"[+] 获取TGC凭证, CAS认证成功!")

        # 访问教务系统获取ticket
        resp = self.session.get(
            self.config["service_url"],
            allow_redirects=False,
        )

        if resp.status_code in (302, 301):
            loc = resp.headers.get("Location", "")
            if "ticket=" in loc:
                self.session.get(loc, allow_redirects=True)
                print("[+] 已进入教务系统!")
                return True
            # 通过CAS获取ticket
            if "cas.swust.edu.cn" in loc:
                cas_resp = self.session.get(loc, allow_redirects=False)
                cas_loc = cas_resp.headers.get("Location", "")
                if cas_loc and "ticket=" in cas_loc:
                    self.session.get(cas_loc, allow_redirects=True)
                    print("[+] 已进入教务系统!")
                    return True

        print("[!] 登录完成但无法验证")
        return True  # 有TGC就算成功

    def get_session(self):
        return self.session


# ============================================================
# 选课模块
# ============================================================
class CourseSelector:
    """选课器 (集成课表解析)"""

    DAY_SHORT = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    DAY_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7}
    SLOT_MAP = {
        "第一讲": (1, 2), "第二讲": (3, 4),
        "第三讲": (5, 6), "第四讲": (7, 8),
        "第五讲": (9, 10), "第六讲": (11, 12),
    }

    # 课程分类: (event名称, 教学班API, 选课API, 显示名称)
    CATEGORIES = [
        ("programTask", "apiPlanTaskTable",     "apiChoosePlanTask",     "计划课程"),
        ("commonTask",  "apiCommonTaskTable",   "apiChooseCommonTask",   "全校通选课"),
        ("sportTask",   "apiSportTaskTable",     "apiChooseSportTask",    "体育项目"),
        ("retakeTask",  "apiRetakePlanTaskTable","apiRetakePlanTask",     "重新学习"),
        ("fixupTask",   "apiFixupPlanTaskTable", "apiFixupPlanTask",      "补选低年级课程"),
    ]

    def __init__(self, config, session):
        self.config = config
        self.session = session
        self.schedule_parser = ScheduleParser(session)
        self.schedule_loaded = False
        self.base_url = "https://matrix.dean.swust.edu.cn/acadmicManager/index.cfm"
        self._course_cache = None  # 缓存的课程列表

    @classmethod
    def _get_category_url(cls, event_name, ct="2"):
        return f"{cls.base_url if hasattr(cls,'base_url') else 'https://matrix.dean.swust.edu.cn/acadmicManager/index.cfm'}?event=chooseCourse:{event_name}&CT={ct}"

    def load_schedule(self):
        """加载当前课表"""
        print("\n[*] 正在获取当前课表...")
        if self.schedule_parser.fetch():
            self.schedule_loaded = True
            self.schedule_parser.print_table()
            self.schedule_parser.print_free()
            return True
        else:
            print("[!] 获取课表失败")
            return False

    def discover_courses(self):
        """探索所有课程分类下的可选课程列表"""
        all_courses = []
        for event_name, api_name, choose_api, label in self.CATEGORIES:
            ct = self.config.get("ct", "2")
            url = f"{self.base_url}?event=chooseCourse:{event_name}&CT={ct}"
            print(f"\n[*] 正在获取{label}页面...")
            resp = self.session.get(url)
            resp.encoding = "utf-8"

            soup = BeautifulSoup(resp.text, "html.parser")
            page_text = soup.get_text()

            if "不是选课时间" in page_text:
                print(f"[!] {label}: 当前不是选课时间")
                time_info = re.findall(
                    r'(\d{4}-\d{2}-\d{2}).*?(\d{2}:\d{2}).*?(\d{2}:\d{2})',
                    page_text,
                )
                for t in time_info:
                    print(f"    选课时间: {t[0]} {t[1]}~{t[2]}")
                continue

            courses = self._parse_course_list(soup, event_name, api_name, choose_api, label)
            all_courses.extend(courses)

        self._course_cache = all_courses
        return all_courses

    def _parse_course_list(self, soup, event_name="", api_name="", choose_api="", category_label=""):
        """从课程页面解析 div.courseShow 课程列表"""
        ct = self.config.get("ct", "2")
        courses = []
        for div in soup.find_all("div", class_="courseShow"):
            cid = div.get("cid", "")
            prop = div.get("prop", "")
            title_div = div.find("div", class_="title")
            if not title_div:
                continue

            name_span = title_div.find("span", class_="name")
            credit_span = title_div.find("span", class_="numeric")
            type_span = title_div.find("span", class_="type")

            name = name_span.get_text(strip=True) if name_span else ""
            credit = credit_span.get_text(strip=True) if credit_span else ""
            ctype = type_span.get_text(strip=True) if type_span else ""

            # 检查是否已选 (trigger有checked类)
            trigger = title_div.find("a", class_="trigger")
            already_selected = False
            if trigger and "checked" in trigger.get("class", []):
                already_selected = True

            courses.append({
                "cid": cid,
                "name": name,
                "credit": credit,
                "type": ctype,
                "prop": prop,
                "already_selected": already_selected,
                "event_name": event_name,
                "api_name": api_name,
                "choose_api": choose_api,
                "category": category_label,
                "ct": ct,
            })

        if courses:
            sel_count = sum(1 for c in courses if c["already_selected"])
            print(f"[+] {category_label}: 发现 {len(courses)} 门课程 (已选{sel_count}门)")
            for c in courses:
                sel = " [已选]" if c["already_selected"] else ""
                print(f"    {c['name']} ({c['credit']}学分 {c['type']}){sel}")
        return courses

    def _fetch_course_sections(self, cid, api_name="apiPlanTaskTable", choose_api="apiChoosePlanTask",
                               category_url=None, event_name="", ct="2"):
        """通过 AJAX API 获取某门课程的教学班列表
        api_name: 如 apiPlanTaskTable / apiSportTaskTable
        choose_api: 如 apiChoosePlanTask / apiChooseSportTask
        event_name: 如 programTask / sportTask，用于构建 Referer
        """
        if category_url is None:
            category_url = f"{self.base_url}?event=chooseCourse:{event_name or 'programTask'}&CT={ct}"
        resp = self.session.post(
            f"{self.base_url}?event=chooseCourse:{api_name}",
            data={"TID": "261", "CID": cid, "seed": str(int(time.time() * 1000))},
            headers={
                "Referer": category_url,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        sections = []

        for row in soup.find_all("tr", class_="editRows"):
            link = row.find("a", href=True)
            if not link:
                continue
            href = link.get("href", "")
            # 解析 chooseCourse('cid','cidx','tid','tt','tsk', 'hash')
            m = re.search(
                r"chooseCourse\('([^']+)','([^']+)','([^']+)','([^']+)','([^']+)',\s*'([^']+)'\)",
                href
            )
            if not m:
                continue

            course_id, course_idx, term_id, task_type, task_id, hash_val = m.groups()
            cols = row.find_all("td")

            teacher = cols[2].get_text(strip=True) if len(cols) > 2 else ""
            total_num = cols[3].get_text(strip=True) if len(cols) > 3 else ""
            seats = cols[4].get_text(strip=True) if len(cols) > 4 else ""
            campus = cols[5].get_text(strip=True) if len(cols) > 5 else ""
            weeks = cols[6].get_text(strip=True) if len(cols) > 6 else ""
            time_str = cols[7].get_text(strip=True) if len(cols) > 7 else ""
            place = cols[8].get_text(strip=True) if len(cols) > 8 else ""

            section = {
                "course_id": course_id,
                "course_idx": course_idx,
                "term_id": term_id,
                "task_type": task_type,
                "task_id": task_id,
                "hash": hash_val,
                "teacher": teacher,
                "total_num": total_num,
                "seats": seats,
                "campus": campus,
                "weeks": weeks,
                "time": time_str,
                "place": place,
                "choose_api": choose_api,
                "event_name": event_name,
                "ct": ct,
            }
            # 解析上课时间
            parsed_time = self._parse_section_time(time_str)
            if parsed_time:
                section["day"] = parsed_time["day"]
                section["periods"] = parsed_time["periods"]
                section["slot_name"] = parsed_time["slot_name"]
            sections.append(section)

        return sections

    def _parse_section_time(self, time_str):
        """解析上课时间字符串, 如 '星期一第三讲' 或 '周一第三讲' → {day:1, periods:(5,6), slot_name:'第三讲'}"""
        if not time_str or time_str == "-":
            return None
        # 兼容 "星期X第Y讲" 和 "周X第Y讲" 两种格式
        m = re.search(r"(?:星期|周)([一二三四五六日])(第?.+讲)", time_str)
        if not m:
            return None
        day = self.DAY_MAP.get(m.group(1))
        slot_name = m.group(2)
        periods = self.SLOT_MAP.get(slot_name)
        if day is None or periods is None:
            return None
        return {"day": day, "periods": periods, "slot_name": slot_name}

    @staticmethod
    def _get_weeks_start(weeks_str):
        """提取周次起始周数字, 如 '02-15' → 2, 解析失败返回999"""
        if not weeks_str or weeks_str == "-":
            return 999
        m = re.search(r'(\d+)', weeks_str)
        return int(m.group(1)) if m else 999

    @classmethod
    def _get_time_preference(cls, slot_name):
        """上课时间优先级: 第2/4讲=0(最优), 第3/5讲=1(次优), 第1讲=2(最次), 未知=999"""
        if not slot_name:
            return 999
        pref = {
            "第二讲": 0, "第四讲": 0,
            "第三讲": 1, "第五讲": 1,
            "第一讲": 2,
        }
        return pref.get(slot_name, 999)

    def filter_by_free_slots(self, courses):
        """根据课表空闲时段过滤课程 (排除时间冲突)"""
        if not self.schedule_loaded:
            return courses
        free_slots = self.schedule_parser.get_free_slots()
        free_set = set()
        for day, ps, pe, sn in free_slots:
            for p in range(ps, pe + 1):
                free_set.add((day, p))

        filtered = []
        for c in courses:
            if "day" not in c or "periods" not in c:
                # 没有时间信息，保留
                filtered.append(c)
                continue
            day = c["day"]
            ps, pe = c["periods"]
            # 检查该时段的所有小节是否空闲
            conflict = False
            for p in range(ps, pe + 1):
                if (day, p) not in free_set:
                    conflict = True
                    break
            if not conflict:
                filtered.append(c)
            else:
                print(f"    [冲突过滤] {c.get('name','?')} ({c.get('course_idx','?')}) 时间冲突")

        if len(filtered) < len(courses):
            print(f"  [*] 空闲过滤: {len(courses)} → {len(filtered)} 门")
        return filtered

    def search_by_keyword(self, target, auto_fetch=True):
        """按关键词搜索课程，支持多条件筛选，返回匹配的教学班列表
        target: {"keyword":"...", "teacher":"...", "time":"...", "weeks":"...", "place":"..."}
        """
        keyword = target.get("keyword", "") if isinstance(target, dict) else str(target)
        expect_teacher = target.get("teacher", "") if isinstance(target, dict) else ""
        expect_time = target.get("time", "") if isinstance(target, dict) else ""
        expect_weeks = target.get("weeks", "") if isinstance(target, dict) else ""
        expect_place = target.get("place", "") if isinstance(target, dict) else ""

        # 也允许 target 指定只搜索某分类
        target_categories = []
        if isinstance(target, dict):
            cats = target.get("categories", [])
            if isinstance(cats, list) and cats:
                target_categories = cats

        print(f"\n[*] 搜索课程: {keyword}")
        filters_active = []
        if expect_teacher:
            filters_active.append(f"教师={expect_teacher}")
        if expect_time:
            filters_active.append(f"时间={expect_time}")
        if expect_weeks:
            filters_active.append(f"周次={expect_weeks}")
        if expect_place:
            filters_active.append(f"地点={expect_place}")
        if filters_active:
            print(f"    筛选条件: {', '.join(filters_active)}")

        # 确保课程列表已加载
        if self._course_cache is None:
            self.discover_courses()

        if not self._course_cache:
            print("[!] 没有可选课程")
            return []

        # 在课程列表中按名称匹配
        matched = []
        for c in self._course_cache:
            if keyword.lower() not in c["name"].lower():
                continue
            # 若 target 指定了 categories，只在该分类下搜索
            if target_categories and c.get("category", "") not in target_categories:
                continue
            matched.append(c)

        if not matched:
            print(f"[!] 未找到匹配课程")
            print("    当前可选课程:")
            for c in self._course_cache:
                print(f"      - {c['name']} ({c.get('type','?')})")
            return []

        print(f"[+] 找到 {len(matched)} 门匹配课程:")
        for m in matched:
            sel = " [已选]" if m["already_selected"] else ""
            print(f"    {m['name']} ({m['credit']}学分 {m['type']}){sel}")

        # 获取每门课程的教学班
        all_sections = []
        for m in matched:
            if m["already_selected"]:
                print(f"    [{m['name']}] 已选, 跳过")
                continue

            print(f"  [*] 获取教学班: {m['name']} (CID:{m['cid']}, {m.get('category','?')})...")
            ct = m.get("ct", self.config.get("ct", "2"))
            sections = self._fetch_course_sections(
                m["cid"],
                api_name=m.get("api_name", "apiPlanTaskTable"),
                choose_api=m.get("choose_api", "apiChoosePlanTask"),
                event_name=m.get("event_name", "programTask"),
                ct=ct,
                category_url=f"{self.base_url}?event=chooseCourse:{m.get('event_name','programTask')}&CT={ct}"
            )
            for s in sections:
                s["name"] = m["name"]
                s["credit"] = m["credit"]
                s["course_type"] = m["type"]
                s["prop"] = m["prop"]

                # 教师筛选
                if expect_teacher and expect_teacher not in s.get("teacher", ""):
                    print(f"      [教师过滤] 课序号{s['course_idx']} ({s.get('teacher','?')})")
                    continue

                # 上课时间筛选 (如 "星期二第三讲"，兼容含/不含"星"两种格式)
                if expect_time:
                    actual_time = s.get("time", "")
                    # 统一去掉"星期"或"周"前缀，使 "星期二第三讲" 和 "周二第三讲" 等价
                    norm_expect = expect_time.replace("星期", "").replace("周", "")
                    norm_actual = actual_time.replace("星期", "").replace("周", "")
                    if norm_expect not in norm_actual:
                        print(f"      [时间过滤] 课序号{s['course_idx']} ({actual_time})")
                        continue

                # 周次筛选 (如 "01-16")
                if expect_weeks and expect_weeks not in s.get("weeks", ""):
                    print(f"      [周次过滤] 课序号{s['course_idx']} ({s.get('weeks','?')})")
                    continue

                # 地点筛选 (如 "西71302")
                if expect_place and expect_place not in s.get("place", ""):
                    print(f"      [地点过滤] 课序号{s['course_idx']} ({s.get('place','?')})")
                    continue

                seats = int(s["seats"]) if s["seats"].isdigit() else 0
                total = int(s["total_num"]) if s["total_num"].isdigit() else 0
                if seats <= 0 and total > 0:
                    print(f"      [满] 课序号{s['course_idx']} 席位:{s['seats']}/{s['total_num']}")
                else:
                    print(f"      [可] 课序号{s['course_idx']} 教师:{s['teacher']} 席位:{s['seats']}/{s['total_num']} 时间:{s['time']} 周次:{s['weeks']} 地点:{s['place']}")
                all_sections.append(s)

        return all_sections

    def select_course(self, section_info):
        """提交选课请求"""
        course_name = section_info.get("name", "?")
        course_idx = section_info.get("course_idx", "?")
        event_name = section_info.get("event_name", "programTask")
        ct = section_info.get("ct", self.config.get("ct", "2"))
        print(f"[*] 选课: {course_name} (课序号:{course_idx}, 分类:{event_name})")

        resp = self.session.post(
            f"{self.base_url}?event=chooseCourse:{section_info.get('choose_api', 'apiChoosePlanTask')}",
            data={
                "CT": ct,
                "TID": section_info.get("term_id", "261"),
                "CID": section_info.get("course_id", ""),
                "CIDX": section_info.get("course_idx", ""),
                "TSK": section_info.get("task_id", ""),
                "TT": section_info.get("task_type", "P"),
                "ST": section_info.get("hash", ""),
                "CP": section_info.get("prop", ""),
                "seed": str(int(time.time() * 1000)),
            },
            headers={
                "Referer": f"{self.base_url}?event=chooseCourse:{event_name}&CT={ct}",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        resp.encoding = "utf-8"

        try:
            result = json.loads(resp.text)
            if result.get("success"):
                print(f"[+] 选课成功! {course_name} (课序号:{course_idx})")
                return True
            else:
                reason = result.get("reason", "未知原因")
                print(f"[!] 选课失败: {reason}")
                return False
        except json.JSONDecodeError:
            print(f"[!] 响应解析失败: {resp.text[:200]}")
            return False

    def run_auto_select(self):
        """自动抢课主循环"""
        targets = self.config.get("target_courses", [])
        if not targets:
            print("[!] 未配置目标课程!")
            print("   请运行 python main.py --setup 进行交互式配置")
            print("   或直接编辑 targets.json 文件")
            return

        # 先加载课表
        self.load_schedule()

        # 加载课程列表
        self.discover_courses()

        print(f"\n[+] 开始抢课! 目标: {len(targets)}门")
        targets.sort(key=lambda t: t.get("priority", 99))
        for t in targets:
            parts = [f"优先级:{t.get('priority',1)}"]
            if t.get("categories"):
                parts.append(f"[{','.join(t['categories'])}]")
            if t.get("teacher"):
                parts.append(f"教师:{t['teacher']}")
            if t.get("time"):
                parts.append(t["time"])
            if t.get("weeks"):
                parts.append(f"{t['weeks']}周")
            if t.get("place"):
                parts.append(t["place"])
            print(f"    - {t.get('keyword', 'N/A')} ({' '.join(parts)})")

        success_count = 0
        for target in targets:
            keyword = target.get("keyword", "")
            if not keyword:
                continue

            for attempt in range(self.config["max_retries"]):
                print(f"\n[*] [{keyword}] 第 {attempt + 1}/{self.config['max_retries']} 次...")

                # 传递完整target字典，由search_by_keyword内部完成所有筛选
                sections = self.search_by_keyword(target, auto_fetch=True)
                if not sections:
                    if attempt < self.config["max_retries"] - 1:
                        time.sleep(self.config["retry_interval"])
                    continue

                # 空闲时段过滤
                sections = self.filter_by_free_slots(sections)

                # 排序: 根据target中time/weeks是否为空选择不同的排序策略
                expect_time = target.get("time", "")
                expect_weeks = target.get("weeks", "")

                def rank_key(s):
                    seats = int(s["seats"]) if s.get("seats", "").isdigit() else 0
                    # 周次排名 (越小越好): 空weeks时按起始周排序, 否则0(已精确筛选)
                    week_rank = self._get_weeks_start(s.get("weeks", "")) if not expect_weeks else 0
                    # 时间排名 (越小越好): 空time时按讲次偏好排序, 否则0(已精确筛选)
                    time_rank = self._get_time_preference(s.get("slot_name", "")) if not expect_time else 0
                    return (week_rank, time_rank, -seats)

                sections.sort(key=rank_key)
                if not expect_weeks or not expect_time:
                    print(f"  [*] 按周次→时间偏好→席位排序 (周次优先)")

                selected = False
                for s in sections:
                    if self.select_course(s):
                        success_count += 1
                        selected = True
                        break
                    #time.sleep(0.3)

                if selected:
                    break

                if attempt < self.config["max_retries"] - 1:
                    time.sleep(self.config["retry_interval"])
            else:
                print(f"[!] [{keyword}] 失败, 已达最大重试")

        print(f"\n[+] 抢课流程结束, 成功 {success_count}/{len(targets)} 门")


# ============================================================
# 定时调度模块
# ============================================================
class Scheduler:
    """定时任务调度器"""

    def __init__(self, config, login_mgr, selector):
        self.config = config
        self.login_mgr = login_mgr
        self.selector = selector

    def wait_until_start(self):
        """等待到选课开始时间"""
        start = self.config["start_time"]
        now = datetime.now()

        if now >= start:
            print("[*] 选课时间已到! 立即开始...")
            return

        wait_seconds = (start - now).total_seconds()

        print(f"\n[*] 选课将于 {start.strftime('%Y-%m-%d %H:%M:%S')} 开始")
        print(f"[*] 当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"[*] 还需等待: {wait_seconds / 3600:.1f} 小时")

        # 精确等待到开始时间
        remaining = (start - datetime.now()).total_seconds()
        if remaining > 0:
            print(f"[*] 等待选课开始...")
            self._countdown(int(remaining))

        print("\n" + "=" * 50)
        print("  🚀 选课开始!")
        print("=" * 50)

    def _countdown(self, seconds):
        """倒计时显示 (每10秒更新)"""
        for i in range(seconds, 0, -1):
            if i % 10 == 0 or i <= 5:
                eta = str(timedelta(seconds=i))
                print(f"  ⏰ 倒计时: {eta}", end="\r")
            time.sleep(1)
        print(" " * 40, end="\r")


# ============================================================
# 主函数
# ============================================================
def _export_schedule_to_file(login_mgr, filepath="课表.md"):
    """拉取当前课表并导出为 Markdown 文件"""
    selector = CourseSelector(CONFIG, login_mgr.get_session())
    if selector.load_schedule():
        selector.schedule_parser.export_markdown(filepath)
        print(f"[+] 课表已更新到: {os.path.abspath(filepath)}")
        return True
    else:
        print("[!] 获取课表失败, 课表未更新")
        return False


def main():
    # 注册 Ctrl+C 信号处理
    signal.signal(signal.SIGINT, _signal_handler)

    # 解析命令行参数
    force_relogin = "--relogin" in sys.argv or "-r" in sys.argv
    export_schedule = "--schedule" in sys.argv or "-s" in sys.argv
    do_setup = "--setup" in sys.argv
    md_filepath = None
    for i, arg in enumerate(sys.argv):
        if arg == "--output" or arg == "-o":
            if i + 1 < len(sys.argv):
                md_filepath = sys.argv[i + 1]
                break

    if force_relogin:
        cookie_file = CONFIG["cookie_file"]
        if os.path.exists(cookie_file):
            os.remove(cookie_file)
            print(f"[+] 已删除旧cookie: {cookie_file}")

    print("=" * 50)
    print("  西南科技大学教务系统 - 抢课脚本")
    print(f"  选课时间: {CONFIG['start_time'].strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    # 1. 初始化登录管理器
    login_mgr = LoginManager(CONFIG)

    # ---- 交互式配置模式 ----
    if do_setup:
        interactive_setup(CONFIG["targets_file"])
        return

    # ---- 重新登录模式 (--relogin) ----
    if force_relogin:
        print("\n[*] 重新登录模式")
        if login_mgr.login():
            print("[+] 登录成功, 凭证已保存")
            _export_schedule_to_file(login_mgr, md_filepath or "课表.md")
        else:
            print("[!] 登录失败")
        if not export_schedule:
            return

    # ---- 仅导出课表模式 ----
    if export_schedule:
        print("\n[*] 课表导出模式")
        if not login_mgr.logged_in:
            print("[*] 登录中...")
            if not login_mgr.login():
                print("[!] 登录失败, 退出")
                return
        selector = CourseSelector(CONFIG, login_mgr.get_session())
        if selector.load_schedule():
            filepath = md_filepath or "课表.md"
            selector.schedule_parser.export_markdown(filepath)
            print(f"[+] 课表已导出到: {os.path.abspath(filepath)}")
        else:
            print("[!] 获取课表失败")
        return

    # ---- 正常抢课流程：优先加载 targets.json ----
    file_targets = load_targets(CONFIG["targets_file"])
    if file_targets is not None:
        CONFIG["target_courses"] = file_targets
        print(f"[+] 已从 {CONFIG['targets_file']} 加载 {len(file_targets)} 门目标课程")

    # 2. 确保登录（等待之前先登好）
    print("\n[*] 登录中...")
    if not login_mgr.logged_in:
        if not login_mgr.login():
            print("[!] 登录失败, 退出")
            return
    save_cookies(login_mgr.get_session(), CONFIG["cookie_file"])

    # 3. 定时等待
    scheduler = Scheduler(CONFIG, login_mgr, None)
    scheduler.wait_until_start()

    # 4. 每次运行先更新课表文件
    _export_schedule_to_file(login_mgr)

    # 5. 获取选课页面, 探索可选课程
    selector = CourseSelector(CONFIG, login_mgr.get_session())
    courses = selector.discover_courses()

    if courses is None:
        print("\n[!] 选课系统尚未开放或无可选课程")
        print("[*] 将每分钟重试一次 (Ctrl+C 退出)...")
        while True:
            for _ in range(60):
                time.sleep(1)
            courses = selector.discover_courses()
            if courses is not None:
                break

    # 6. 执行选课
    selector.run_auto_select()

    print("\n[+] 抢课流程结束")
    save_cookies(login_mgr.get_session(), CONFIG["cookie_file"])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[+] 已安全退出")
        sys.exit(0)
