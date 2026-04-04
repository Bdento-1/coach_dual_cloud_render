#!/usr/bin/env python3
"""
Font Switch Reminder — macOS Menu Bar App
==========================================
แสดงภาษาปัจจุบันบน menu bar + เสียงเตือนเมื่อสลับภาษาหรืออยู่นานเกินไป

ติดตั้ง:
    pip3 install rumps

รัน:
    python3 font_reminder.py

เพิ่มใน Login Items:
    System Settings → General → Login Items → เพิ่ม script นี้
"""

import rumps
import subprocess
import threading
import time
import os
import sys

# ── เสียงระบบที่มีบน macOS ──────────────────────────────────
SOUND_SWITCH = "/System/Library/Sounds/Tink.aiff"       # เสียงสั้น เมื่อสลับภาษา
SOUND_WARN   = "/System/Library/Sounds/Sosumi.aiff"     # เสียงเตือน เมื่ออยู่นานเกิน
SOUND_ALERT  = "/System/Library/Sounds/Glass.aiff"      # เสียงแจ้งเตือนเร่งด่วน

REMINDER_OPTIONS = [1, 2, 3, 5, 10, 15, 30]  # ตัวเลือกนาที


def get_input_source() -> str:
    """คืนค่า 'th' หรือ 'en' ตาม input source ปัจจุบัน"""
    try:
        result = subprocess.run(
            ["defaults", "read", "com.apple.HIToolbox",
             "AppleCurrentKeyboardLayoutInputSourceID"],
            capture_output=True, text=True, timeout=1
        )
        return "th" if "Thai" in result.stdout else "en"
    except Exception:
        return "en"


def play_sound(path: str):
    """เล่นเสียงแบบ non-blocking"""
    subprocess.Popen(["afplay", path],
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


class FontSwitchApp(rumps.App):
    def __init__(self):
        super().__init__("🇺🇸", quit_button=None)

        self.current_lang    = get_input_source()
        self.last_switch_time = time.time()
        self.reminder_min    = 5
        self.sound_enabled   = True
        self.alert_on_switch = True

        # ── อัปเดต icon เริ่มต้น ──
        self._update_icon()

        # ── เมนู ──
        self.lang_item    = rumps.MenuItem("", callback=None)
        self.sound_item   = rumps.MenuItem("🔔 เสียงเตือน: เปิด",   callback=self.toggle_sound)
        self.switch_item  = rumps.MenuItem("🔁 เสียงตอนสลับ: เปิด", callback=self.toggle_switch_sound)
        self.timer_item   = rumps.MenuItem(f"⏱ เตือนทุก {self.reminder_min} นาที", callback=self.cycle_timer)
        quit_item         = rumps.MenuItem("❌ ออก", callback=rumps.quit_application)

        self.menu = [
            self.lang_item,
            None,
            self.sound_item,
            self.switch_item,
            self.timer_item,
            None,
            quit_item,
        ]
        self._update_lang_item()

        # ── Thread ตรวจ input source ──
        t = threading.Thread(target=self._monitor_loop, daemon=True)
        t.start()

    # ── helpers ────────────────────────────────────────────────
    def _update_icon(self):
        self.title = "🇹🇭" if self.current_lang == "th" else "🇺🇸"

    def _update_lang_item(self):
        lang_str = "ภาษาไทย 🇹🇭" if self.current_lang == "th" else "English 🇺🇸"
        self.lang_item.title = f"ตอนนี้: {lang_str}"

    # ── monitor loop ───────────────────────────────────────────
    def _monitor_loop(self):
        while True:
            lang = get_input_source()

            # ตรวจสอบการสลับภาษา
            if lang != self.current_lang:
                self.current_lang     = lang
                self.last_switch_time = time.time()
                self._update_icon()
                self._update_lang_item()

                if self.alert_on_switch and self.sound_enabled:
                    play_sound(SOUND_SWITCH)

            # ตรวจสอบว่าอยู่นานเกิน reminder_min หรือยัง
            idle_secs = time.time() - self.last_switch_time
            if idle_secs >= self.reminder_min * 60:
                self.last_switch_time = time.time()  # reset ไม่ให้เตือนซ้ำ
                lang_str = "ไทย 🇹🇭" if self.current_lang == "th" else "English 🇺🇸"
                mins = self.reminder_min

                if self.sound_enabled:
                    play_sound(SOUND_WARN)
                    time.sleep(0.6)
                    play_sound(SOUND_WARN)  # เตือน 2 ครั้ง

                rumps.notification(
                    title="⌨️ Font Switch Reminder",
                    subtitle=f"อยู่ใน {lang_str} นาน {mins} นาทีแล้ว",
                    message="ตรวจสอบว่าภาษาถูกต้องก่อนพิมพ์ต่อนะ!",
                    sound=False,
                )

            time.sleep(0.8)  # ตรวจทุก 0.8 วินาที

    # ── menu callbacks ─────────────────────────────────────────
    def toggle_sound(self, sender):
        self.sound_enabled = not self.sound_enabled
        icon = "🔔" if self.sound_enabled else "🔕"
        state = "เปิด" if self.sound_enabled else "ปิด"
        sender.title = f"{icon} เสียงเตือน: {state}"

    def toggle_switch_sound(self, sender):
        self.alert_on_switch = not self.alert_on_switch
        icon = "🔁" if self.alert_on_switch else "🔇"
        state = "เปิด" if self.alert_on_switch else "ปิด"
        sender.title = f"{icon} เสียงตอนสลับ: {state}"

    def cycle_timer(self, sender):
        idx = REMINDER_OPTIONS.index(self.reminder_min) \
              if self.reminder_min in REMINDER_OPTIONS else 3
        self.reminder_min = REMINDER_OPTIONS[(idx + 1) % len(REMINDER_OPTIONS)]
        self.last_switch_time = time.time()  # reset timer
        sender.title = f"⏱ เตือนทุก {self.reminder_min} นาที"


if __name__ == "__main__":
    # ต้องการ macOS
    if sys.platform != "darwin":
        print("❌ แอพนี้ใช้ได้บน macOS เท่านั้น")
        sys.exit(1)

    print("✅ Font Switch Reminder เริ่มทำงาน — ดูที่ menu bar บนขวา")
    FontSwitchApp().run()
