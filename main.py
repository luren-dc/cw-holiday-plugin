from typing import Optional, TypedDict, cast
import requests
import os
import json
import re
from datetime import datetime
from PyQt5.QtCore import QTimer, QThread, Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QWidget
from loguru import logger
from qfluentwidgets import FluentIcon, IconWidget

from .ClassWidgets.base import PluginBase

WIDGET_CODE = "widget_holiday.ui"
WIDGET_NAME = "假期倒计时"
WIDGET_WIDTH = 280
API_URL = "https://fastly.jsdelivr.net/gh/NateScarlet/holiday-cn@master/{}.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}


class Holiday(TypedDict):
    name: str
    date: str
    days_left: int


class HolidayFetcher(QThread):
    data_ready = pyqtSignal(dict)
    fetch_failed = pyqtSignal(Exception)

    def __init__(self, cache_dir: str):
        super().__init__()
        self.current_year = datetime.now().year
        self.cache_dir = cache_dir
        self.required_years = [self.current_year, self.current_year + 1]
        os.makedirs(self.cache_dir, exist_ok=True)

    def run(self):
        try:
            data = []
            # 仅下载缺失的年份数据
            for year in self.required_years:
                cache_file = os.path.join(self.cache_dir, f"holidays_{year}.json")
                if not os.path.exists(cache_file):
                    self._fetch_and_cache(year, cache_file)
                data += self._load_cache(cache_file)

            # 清理旧缓存
            self._cleanup_old_cache()

            # 处理数据逻辑
            now = datetime.now()
            nearest = None
            for day in data:
                date = datetime.strptime(day["date"], "%Y-%m-%d")
                if date >= now and day["isOffDay"]:
                    days_left = (date - now).days
                    if not nearest or days_left < nearest["days_left"]:
                        nearest = {
                            "name": day["name"],
                            "date": day["date"],
                            "days_left": days_left,
                        }
            self.data_ready.emit(nearest or {})
        except Exception as e:
            self.fetch_failed.emit(e)

    def _fetch_and_cache(self, year: int, cache_path: str):
        """下载并缓存数据"""
        try:
            response = requests.get(API_URL.format(year), headers=HEADERS, timeout=30)
            response.raise_for_status()
            data = response.json()

            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"已下载并缓存{year}年数据")
            return data
        except Exception as e:
            logger.error(f"下载{year}年数据失败: {e}")
            raise

    def _load_cache(self, cache_path: str) -> list:
        """加载本地缓存数据"""
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f).get("days", [])
        except Exception as e:
            logger.error(f"读取缓存失败 {cache_path}: {e}")
            return []

    def _cleanup_old_cache(self):
        """清理非必要缓存"""
        pattern = re.compile(r"holidays_(\d{4})\.json")
        for filename in os.listdir(self.cache_dir):
            match = pattern.match(filename)
            if match:
                year = int(match.group(1))
                if year not in self.required_years:
                    os.remove(os.path.join(self.cache_dir, filename))
                    logger.info(f"已清理历史缓存: {filename}")


class Plugin(PluginBase):
    def __init__(self, cw_contexts, method):
        super().__init__(cw_contexts, method)
        self.method.register_widget(WIDGET_CODE, WIDGET_NAME, WIDGET_WIDTH)
        self.cache_dir = os.path.join(self.PATH, "holiday_cache")
        self.retry_timer = QTimer()
        self.retry_timer.timeout.connect(self.update_holiday)
        self.widget: Optional[QWidget] = None
        self.content: Optional[QLabel] = None
        self.icon: Optional[IconWidget] = None

    def execute(self):
        """插件启动时执行"""
        self._init_ui()
        os.makedirs(self.cache_dir, exist_ok=True)
        self.update_holiday()

    def _init_ui(self):
        self.widget = cast(QWidget, self.method.get_widget(WIDGET_CODE))
        content_layout = self.widget.findChild(QHBoxLayout, "contentLayout")
        self.content = content_layout.findChild(QLabel, "content")
        self.icon = IconWidget()
        content_layout.insertItem(0, self.icon)
        content_layout.setAlignment(self.icon, Qt.AlignmentFlag.AlignCenter)

    def _update_ui(self, holiday: Holiday | None):
        self.icon.setVisible(False)
        if not holiday:
            self.method.change_widget_content(
                widget_code=WIDGET_CODE, title="假期正在装载中...", content="0 天"
            )
            self.icon.setIcon(FluentIcon.ASTERISK)
            self.icon.setVisible(True)
            return

        self.method.change_widget_content(
            widget_code=WIDGET_CODE,
            title=f"距离 {holiday['name']} 还有",
            content=f"{holiday['days_left']} 天",
        )
        if holiday["days_left"] < 2:
            self.icon.setIcon(FluentIcon.CALORIES)
            self.icon.setVisible(True)

    def update_holiday(self):
        self.retry_timer.stop()
        self.worker_thread = HolidayFetcher(self.cache_dir)
        self.worker_thread.data_ready.connect(self._update_ui)
        self.worker_thread.fetch_failed.connect(
            lambda e: logger.error(f"数据获取失败: {e}")
        )
        self.worker_thread.start()
