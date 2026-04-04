#!/usr/bin/env python3
"""
Font Switch Reminder — Global Keyboard Monitor
===============================================
ตรวจจับทุก keystroke ทั่วทั้ง iMac แบบ real-time
ถ้าพิมพ์ผิดภาษา (garbage text) → เสียงเตือนทันที

ต้องการ:
    pip install rumps pyobjc-framework-Cocoa pyobjc-framework-Quartz

ต้องการสิทธิ์ Accessibility:
    System Settings → Privacy & Security → Accessibility → เปิดให้ Terminal (หรือ app นี้)
"""

import rumps
import subprocess
import threading
import time
import os
import sys

from AppKit import NSEvent, NSKeyDownMask

# ── Thai character sets ──────────────────────────────────────
# Tone marks + diacritics ที่ต้องตามหลัง consonant เสมอในภาษาไทยจริง
THAI_TONE_MARKS = set('่้๊๋็ํฺ')
# Consonants ภาษาไทย
THAI_CONSONANTS = set('กขฃคฅฆงจฉชซฌญฎฏฐฑฒณดตถทธนบปผฝพฟภมยรลวศษสหฬอฮ')
EN_VOWELS       = set('aeiouAEIOU')

# ── Tuning ───────────────────────────────────────────────────
BUFFER_SIZE       = 6     # ตรวจสอบจาก 6 ตัวอักษรล่าสุด
MIN_CHARS         = 4     # ต้องมีอย่างน้อย N ตัวก่อนตรวจ
GARBAGE_COOLDOWN  = 3.0   # วินาที — ไม่เตือนซ้ำถี่เกินนี้
REMINDER_OPTIONS  = [1, 2, 3, 5, 10, 15, 30]

SOUND_GARBAGE = "/System/Library/Sounds/Sosumi.aiff"
SOUND_SWITCH  = "/System/Library/Sounds/Tink.aiff"
SOUND_IDLE    = "/System/Library/Sounds/Glass.aiff"


# ── helpers ──────────────────────────────────────────────────
def is_thai(c: str) -> bool:
    return '\u0E00' <= c <= '\u0E7F'

def is_latin(c: str) -> bool:
    return c.isalpha() and not is_thai(c)

def play_sound(path: str):
    subprocess.Popen(["afplay", path],
                     stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)

def get_input_source() -> str:
    try:
        r = subprocess.run(
            ["defaults", "read", "com.apple.HIToolbox",
             "AppleCurrentKeyboardLayoutInputSourceID"],
            capture_output=True, text=True, timeout=1
        )
        return "th" if "Thai" in r.stdout else "en"
    except Exception:
        return "en"

def is_garbage(chars: list) -> bool:
    """
    คืน True ถ้าตัวอักษรล่าสุดดูเหมือนพิมพ์ผิดภาษา

    วิธีตรวจ Thai garbage (พิมพ์ EN ขณะ Thai mode):
      - ตัวแรกของ sequence เป็น tone mark/diacritic (เช่น ้ จาก 'h')
        → ภาษาไทยจริงไม่มีทางเริ่มด้วย tone mark
      - หรือมี tone mark ติดกัน 2 ตัว

    วิธีตรวจ EN garbage (พิมพ์ TH layout ขณะ EN mode):
      - Latin letters ล้วนๆ แต่ไม่มี vowel เลย (เช่น lsyfd)
    """
    th = [c for c in chars if is_thai(c)]
    en = [c for c in chars if is_latin(c)]
    total = len(th) + len(en)

    if total < MIN_CHARS:
        return False

    # ── Thai garbage ──
    if len(th) >= MIN_CHARS and len(th) > len(en):
        # Rule 1: ขึ้นต้นด้วย tone mark → garbage แน่นอน
        # เช่น "hello" ใน Thai mode → ้ำสสน (้ ขึ้นต้น = invalid)
        if th[0] in THAI_TONE_MARKS:
            return True
        # Rule 2: tone mark ติดกัน 2 ตัว
        for i in range(len(th) - 1):
            if th[i] in THAI_TONE_MARKS and th[i+1] in THAI_TONE_MARKS:
                return True
        # Rule 3: ไม่มี consonant เลย (ล้วนแต่ mark/vowel)
        consonant_count = sum(1 for c in th if c in THAI_CONSONANTS)
        if consonant_count == 0:
            return True

    # ── EN garbage (Thai Kedmanee layout typed in EN mode) ──
    if len(en) >= MIN_CHARS and len(en) > len(th):
        vowel_count = sum(1 for c in en if c in EN_VOWELS)
        if vowel_count / len(en) < 0.08:
            return True

    return False


# ════════════════════════════════════════════════════════════
class FontSwitchApp(rumps.App):

    def __init__(self):
        super().__init__("🇺🇸", quit_button=None)

        self.current_lang     = get_input_source()
        self.last_switch_time = time.time()
        self.last_alert_time  = 0.0
        self.reminder_min     = 5
        self.sound_enabled    = True
        self.monitoring       = False

        self.recent_chars: list[str] = []

        self._update_icon()

        # ── menu items ──
        self.lang_item   = rumps.MenuItem("", callback=None)
        self.mon_item    = rumps.MenuItem("🔍 ตรวจ garbage: ปิด", callback=self.toggle_monitor)
        self.sound_item  = rumps.MenuItem("🔔 เสียงเตือน: เปิด",  callback=self.toggle_sound)
        self.timer_item  = rumps.MenuItem(f"⏱ เตือนทุก {self.reminder_min} นาที", callback=self.cycle_timer)
        self.access_item = rumps.MenuItem("⚙️ วิธีให้สิทธิ์ Accessibility", callback=self.open_accessibility)
        quit_item        = rumps.MenuItem("❌ ออก", callback=rumps.quit_application)

        self.menu = [
            self.lang_item,
            None,
            self.mon_item,
            self.sound_item,
            self.timer_item,
            None,
            self.access_item,
            None,
            quit_item,
        ]
        self._update_lang_item()

        # ── thread ตรวจ input source + idle timer ──
        t = threading.Thread(target=self._source_loop, daemon=True)
        t.start()

        # ── เริ่ม global keyboard monitor บน main thread ──
        self._start_global_monitor()

    # ── icon / label ────────────────────────────────────────
    def _update_icon(self):
        self.title = "🇹🇭" if self.current_lang == "th" else "🇺🇸"

    def _update_lang_item(self):
        lang_str = "ภาษาไทย 🇹🇭" if self.current_lang == "th" else "English 🇺🇸"
        self.lang_item.title = f"ตอนนี้: {lang_str}"

    # ── global keyboard monitor ──────────────────────────────
    def _start_global_monitor(self):
        """ลงทะเบียน global event tap ผ่าน NSEvent (ต้องการสิทธิ์ Accessibility)"""
        try:
            self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                NSKeyDownMask,
                self._handle_key_event
            )
            self.monitoring = True
            self.mon_item.title = "🔍 ตรวจ garbage: เปิด ✅"
            print("✅ Global keyboard monitor เริ่มทำงาน")
        except Exception as e:
            print(f"⚠️ ไม่สามารถเริ่ม keyboard monitor: {e}")
            print("   → ต้องให้สิทธิ์ Accessibility ก่อน")
            self.mon_item.title = "🔍 ตรวจ garbage: ❌ ต้องการสิทธิ์"

    def _handle_key_event(self, event):
        """รับทุก keystroke จากทุกโปรแกรมบน iMac"""
        if not self.monitoring:
            return

        chars = event.characters()
        if not chars:
            return

        for c in chars:
            if c.isprintable() and not c.isspace() and ord(c) > 31:
                self.recent_chars.append(c)
                if len(self.recent_chars) > BUFFER_SIZE:
                    self.recent_chars.pop(0)

        # ตรวจ garbage
        if is_garbage(self.recent_chars):
            now = time.time()
            if now - self.last_alert_time >= GARBAGE_COOLDOWN:
                self.last_alert_time = now
                self.recent_chars.clear()
                self._trigger_garbage_alert()

    def _trigger_garbage_alert(self):
        lang_str = "ไทย 🇹🇭" if self.current_lang == "th" else "English 🇺🇸"
        if self.sound_enabled:
            play_sound(SOUND_GARBAGE)
            threading.Timer(0.5, lambda: play_sound(SOUND_GARBAGE)).start()

        rumps.notification(
            title="⚠️ ลืมเปลี่ยนภาษา!",
            subtitle=f"Input อยู่ใน {lang_str} — กด Caps Lock ด่วน!",
            message="ตรวจพบว่าพิมพ์ข้อความมั่วไม่มีความหมาย",
            sound=False,
        )

    # ── background loop: input source + idle timer ───────────
    def _source_loop(self):
        while True:
            lang = get_input_source()

            if lang != self.current_lang:
                self.current_lang     = lang
                self.last_switch_time = time.time()
                self.recent_chars.clear()  # reset buffer เมื่อสลับภาษา
                self._update_icon()
                self._update_lang_item()

                if self.sound_enabled:
                    play_sound(SOUND_SWITCH)

            # idle timer
            idle = time.time() - self.last_switch_time
            if idle >= self.reminder_min * 60:
                self.last_switch_time = time.time()
                lang_str = "ไทย 🇹🇭" if self.current_lang == "th" else "English 🇺🇸"
                if self.sound_enabled:
                    play_sound(SOUND_IDLE)
                    time.sleep(0.6)
                    play_sound(SOUND_IDLE)
                rumps.notification(
                    title="⏰ ยังไม่ได้เปลี่ยนภาษา",
                    subtitle=f"อยู่ใน {lang_str} นาน {self.reminder_min} นาทีแล้ว",
                    message="ตรวจสอบว่าภาษาถูกต้องก่อนพิมพ์ต่อ",
                    sound=False,
                )

            time.sleep(0.8)

    # ── menu callbacks ────────────────────────────────────────
    def toggle_monitor(self, sender):
        self.monitoring = not self.monitoring
        state = "เปิด ✅" if self.monitoring else "ปิด"
        sender.title = f"🔍 ตรวจ garbage: {state}"

    def toggle_sound(self, sender):
        self.sound_enabled = not self.sound_enabled
        icon  = "🔔" if self.sound_enabled else "🔕"
        state = "เปิด" if self.sound_enabled else "ปิด"
        sender.title = f"{icon} เสียงเตือน: {state}"

    def cycle_timer(self, sender):
        idx = REMINDER_OPTIONS.index(self.reminder_min) \
              if self.reminder_min in REMINDER_OPTIONS else 3
        self.reminder_min = REMINDER_OPTIONS[(idx + 1) % len(REMINDER_OPTIONS)]
        self.last_switch_time = time.time()
        sender.title = f"⏱ เตือนทุก {self.reminder_min} นาที"

    def open_accessibility(self, _):
        subprocess.run([
            "open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
        ])


# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    if sys.platform != "darwin":
        print("❌ ใช้ได้บน macOS เท่านั้น")
        sys.exit(1)

    print("✅ Font Switch Reminder เริ่มทำงาน")
    print("   → ดูที่ menu bar มุมขวาบน")
    print("   → ถ้าไม่มีเสียงเตือน garbage → ต้องให้สิทธิ์ Accessibility")
    print("   → คลิก menu bar icon → '⚙️ วิธีให้สิทธิ์ Accessibility'")

    FontSwitchApp().run()
