# -*- coding: utf-8 -*-
"""
课表解析器 - 根据实际HTML结构精确解析
"""
from bs4 import BeautifulSoup
import requests


class ScheduleParser:

    DAY_NAMES = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期天"]
    DAY_SHORT = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    SLOT_TO_PERIODS = {
        "第一讲": (1, 2), "第二讲": (3, 4),
        "第三讲": (5, 6), "第四讲": (7, 8),
        "第五讲": (9, 10), "第六讲": (11, 12),
    }

    def __init__(self, session=None):
        self.session = session
        self.courses = []
        self.schedule = {}

    def fetch(self, url=None):
        if url is None:
            url = "https://matrix.dean.swust.edu.cn/acadmicManager/index.cfm?event=chooseCourse:courseTable"
        resp = self.session.get(url)
        resp.encoding = "utf-8"
        return self.parse_html(resp.text)

    def parse_html(self, html):
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table", class_="UICourseTable")
        if not table:
            return False
        day_cols = self._parse_thead(table)
        self._parse_tbody(table, day_cols)
        return len(self.courses) > 0

    def _parse_thead(self, table):
        thead = table.find("thead")
        if not thead:
            return {}
        day_cols = {}
        for i, td in enumerate(thead.find_all("td")):
            text = td.get_text(strip=True)
            for dnum, dname in enumerate(self.DAY_NAMES, 1):
                if dname in text:
                    day_cols[i] = dnum
                    break
        return day_cols

    def _parse_tbody(self, table, day_cols):
        tbody = table.find("tbody")
        if not tbody:
            return
        rows = tbody.find_all("tr")
        period_tag = None
        rowspan_skip = False

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            # rowspan handling: row with rowspan is full-width, next row is short
            if cells[0].get("rowspan"):
                period_tag = cells[0].get_text(strip=True)
                rowspan_skip = False
                slot_idx = 1
            else:
                rowspan_skip = True
                slot_idx = 0

            slot_name = cells[slot_idx].get_text(strip=True)
            if slot_name not in self.SLOT_TO_PERIODS:
                continue

            periods = self.SLOT_TO_PERIODS[slot_name]
            data_start = slot_idx + 1

            for i, cell in enumerate(cells[data_start:]):
                col_idx = data_start + i + (1 if rowspan_skip else 0)
                day = day_cols.get(col_idx - 1)  # header列号 = body列号 - 1
                if day is None:
                    continue

                info = self._extract_course(cell)
                if info:
                    info["day"] = day
                    info["slot_name"] = slot_name
                    info["period_tag"] = period_tag
                    info["periods"] = periods
                    self.courses.append(info)
                    for p in range(periods[0], periods[1] + 1):
                        self.schedule[(day, p)] = info

    def _extract_course(self, cell):
        div = cell.find("div", class_="lecture")
        if not div:
            return None
        c = div.find("span", class_="course")
        t = div.find("span", class_="teacher")
        w = div.find("span", class_="week")
        p = div.find("span", class_="place")
        if not c:
            return None
        return {
            "name": c.get_text(strip=True),
            "teacher": t.get_text(strip=True) if t else "",
            "weeks": w.get_text(strip=True) if w else "",
            "place": p.get_text(strip=True) if p else "",
        }

    def is_free(self, day, start, end):
        for p in range(start, end + 1):
            if (day, p) in self.schedule:
                return False
        return True

    def get_free_slots(self):
        free = []
        for day in range(1, 8):
            for sn, (ps, pe) in self.SLOT_TO_PERIODS.items():
                if self.is_free(day, ps, pe):
                    free.append((day, ps, pe, sn))
        return free

    def get_free_by_day(self):
        by_day = {}
        for day, ps, pe, sn in self.get_free_slots():
            by_day.setdefault(day, []).append(sn)
        return by_day

    def print_table(self):
        print("\n" + "=" * 85)
        print("                          [当前课表] 2026-2027-1学期")
        print("=" * 85)
        header = f"{'':<8}" + "".join(f"{d:<11}" for d in self.DAY_SHORT)
        print(header)
        print("-" * 85)
        for sn in self.SLOT_TO_PERIODS:
            ps, pe = self.SLOT_TO_PERIODS[sn]
            row = f"{sn:<8}"
            for day in range(1, 8):
                c = None
                for p in range(ps, pe + 1):
                    c = c or self.schedule.get((day, p))
                row += f"{c['name'][:8] if c else chr(8212):<11}"
            print(row)
        print("-" * 85)

    def print_detail(self):
        print("\n[已选课程详情]:")
        print("-" * 70)
        for c in sorted(self.courses, key=lambda x: (x["day"], x["periods"][0])):
            dn = self.DAY_SHORT[c["day"] - 1]
            ps, pe = c["periods"]
            print(f"\n  * {c['name']}")
            print(f"     教师: {c['teacher']}")
            print(f"     时间: {dn} {c['slot_name']} (第{ps}-{pe}节)")
            print(f"     周次: {c['weeks']}")
            print(f"     教室: {c['place']}")

    def print_free(self):
        by_day = self.get_free_by_day()
        print("\n[空闲时段] (可排课):")
        print("-" * 60)
        for day in range(1, 8):
            dn = self.DAY_SHORT[day - 1]
            slots = by_day.get(day, [])
            print(f"  {dn}: {'、'.join(slots) if slots else '(满)'}")

    def print_summary(self):
        self.print_table()
        self.print_detail()
        self.print_free()

    # ================================================================
    # Markdown 导出
    # ================================================================
    def export_markdown(self, filepath="课表.md"):
        """将课表导出为 Markdown 文件"""
        lines = []
        lines.append("# 📅 当前课表 — 2026-2027-1学期")
        lines.append("")

        # ---- 表格视图 ----
        lines.append("## 一、课表总览")
        lines.append("")
        header_cols = [""] + list(self.DAY_SHORT)
        lines.append("| " + " | ".join(header_cols) + " |")
        lines.append("|" + "|".join([":---"] * len(header_cols)) + "|")

        for sn, (ps, pe) in self.SLOT_TO_PERIODS.items():
            row_cells = [sn]
            for day in range(1, 8):
                c = None
                for p in range(ps, pe + 1):
                    c = c or self.schedule.get((day, p))
                if c:
                    row_cells.append(c["name"])
                else:
                    row_cells.append("—")
            lines.append("| " + " | ".join(row_cells) + " |")

        lines.append("")

        # ---- 课程详情 ----
        lines.append("## 二、已选课程详情")
        lines.append("")
        idx = 0
        for c in sorted(self.courses, key=lambda x: (x["day"], x["periods"][0])):
            idx += 1
            dn = self.DAY_SHORT[c["day"] - 1]
            ps, pe = c["periods"]
            lines.append(f"### {idx}. {c['name']}")
            lines.append("")
            lines.append(f"| 项目 | 内容 |")
            lines.append(f"| :--- | :--- |")
            lines.append(f"| 教师 | {c['teacher']} |")
            lines.append(f"| 时间 | {dn} {c['slot_name']}（第{ps}-{pe}节）|")
            lines.append(f"| 周次 | {c['weeks']} |")
            lines.append(f"| 教室 | {c['place']} |")
            lines.append("")

        # ---- 空闲时段 ----
        lines.append("## 三、空闲时段")
        lines.append("")
        lines.append("| 星期 | 空闲讲次 |")
        lines.append("| :--- | :--- |")
        by_day = self.get_free_by_day()
        for day in range(1, 8):
            dn = self.DAY_SHORT[day - 1]
            slots = by_day.get(day, [])
            cell = "、".join(slots) if slots else "（满）"
            lines.append(f"| {dn} | {cell} |")
        lines.append("")

        content = "\n".join(lines)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"\n[+] 课表已导出到: {filepath}")
        return filepath
