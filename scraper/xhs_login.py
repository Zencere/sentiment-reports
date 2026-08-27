# -*- coding: utf-8 -*-
"""
小红书一次性扫码登录脚本。

用法:
    python xhs_login.py

流程:
    1. 弹出可见浏览器窗口，打开小红书首页
    2. 请在弹出的窗口中用手机扫码登录
    3. 登录成功后，登录态自动保存到 .xhs_profile/ 目录
    4. 之后运行采集器（xiaohongshu_scraper.py 或 run_scraper.py -s xiaohongshu）
       会自动复用该登录态，无需再次登录

说明:
    - 登录态（Cookie + localStorage）保存在 scraper/.xhs_profile/ 目录
    - 建议将该目录加入 .gitignore，避免泄露个人 Cookie
    - web_session 过期后需重新运行本脚本
"""

import os
import sys
import logging

_current_dir = os.path.dirname(os.path.abspath(__file__))
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

from xiaohongshu_scraper import XiaohongshuScraper, DEFAULT_PROFILE_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> None:
    scraper = XiaohongshuScraper(headless=False)

    # 若之前已登录且仍有 web_session，则提示
    scraper._start()
    if scraper.is_logged_in():
        print("\n检测到已存在登录态，无需重复登录。")
        print(f"Profile 目录: {DEFAULT_PROFILE_DIR}")
        scraper.close()
        return

    scraper.close()

    ok = XiaohongshuScraper(headless=False).login(timeout=240)
    if ok:
        print("\n[成功] 登录完成，登录态已保存。")
        print(f"Profile 目录: {DEFAULT_PROFILE_DIR}")
        print("现在可以运行: python run_scraper.py -s xiaohongshu")
    else:
        print("\n[失败] 登录超时或未完成，请重新运行本脚本。")


if __name__ == "__main__":
    main()