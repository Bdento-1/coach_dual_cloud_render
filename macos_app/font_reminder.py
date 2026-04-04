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
import ctypes
import ctypes.util

from AppKit import NSEvent, NSKeyDownMask
from Foundation import NSDistributedNotificationCenter, NSObject, NSRunLoop, NSDate

# ── Carbon TIS: อ่าน input source โดยตรง ไม่ spawn process (~1ms) ──────
def _setup_tis():
    try:
        cf  = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
        car = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Carbon"))

        car.TISCopyCurrentKeyboardInputSource.restype  = ctypes.c_void_p
        car.TISCopyCurrentKeyboardInputSource.argtypes = []
        car.TISGetInputSourceProperty.restype  = ctypes.c_void_p
        car.TISGetInputSourceProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cf.CFStringGetCString.restype  = ctypes.c_bool
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                          ctypes.c_long, ctypes.c_uint32]
        cf.CFRelease.restype  = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]

        prop_id = ctypes.c_void_p.in_dll(car, "kTISPropertyInputSourceID")
        return cf, car, prop_id
    except Exception as e:
        print(f"[TIS setup] fallback to defaults ({e})")
        return None, None, None

_CF, _CAR, _TIS_PROP_ID = _setup_tis()

def get_input_source() -> str:
    """อ่าน input source ปัจจุบัน — ใช้ TIS API โดยตรงถ้าทำได้ ไม่งั้น fallback"""
    if _CAR is not None:
        try:
            src = _CAR.TISCopyCurrentKeyboardInputSource()
            if src:
                ref = _CAR.TISGetInputSourceProperty(src, _TIS_PROP_ID)
                result = "en"
                if ref:
                    buf = ctypes.create_string_buffer(256)
                    _CF.CFStringGetCString(ref, buf, 256, 0x08000100)  # UTF-8
                    name = buf.value.decode("utf-8", errors="ignore")
                    result = "th" if "Thai" in name else "en"
                _CF.CFRelease(src)
                return result
        except Exception:
            pass
    # fallback
    try:
        r = subprocess.run(
            ["defaults", "read", "com.apple.HIToolbox",
             "AppleCurrentKeyboardLayoutInputSourceID"],
            capture_output=True, text=True, timeout=1
        )
        return "th" if "Thai" in r.stdout else "en"
    except Exception:
        return "en"

# ── Thai character sets ──────────────────────────────────────
THAI_TONE_MARKS = set('่้๊๋็ํฺ')
THAI_CONSONANTS = set('กขฃคฅฆงจฉชซฌญฎฏฐฑฒณดตถทธนบปผฝพฟภมยรลวศษสหฬอฮ')
EN_VOWELS       = set('aeiouAEIOU')

# Kedmanee: Thai char → EN key (lowercase)
TH_TO_EN_KEY = {
    'ๆ':'q','ไ':'w','ำ':'e','พ':'r','ะ':'t','ั':'y','ี':'u','ร':'i','น':'o','ย':'p',
    'ฟ':'a','ห':'s','ก':'d','ด':'f','เ':'g','้':'h','่':'j','า':'k','ส':'l','ว':';','ง':"'",
    'ผ':'z','ป':'x','แ':'c','อ':'v','ิ':'b','ื':'n','ท':'m','ม':',','ใ':'.','ฝ':'/'
}
# Keys ที่ใช้ใน Thai Kedmanee แต่ไม่ปรากฏใน English word ปกติ
TH_SPECIAL_KEYS = set(";',./-")

# ── Tuning ──────────────────────────────────────────────────
MIN_CHARS        = 4
GARBAGE_COOLDOWN = 0.4
REMINDER_OPTIONS = [1, 2, 3, 5, 10, 15, 30]

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

def is_garbage(chars: list) -> bool:
    th = [c for c in chars if is_thai(c)]
    en = [c for c in chars if is_latin(c)]

    # ── Thai garbage ──────────────────────────────────────────
    if len(th) >= MIN_CHARS and len(th) > len(en):
        # Rule A: ขึ้นต้นด้วย tone mark
        if th[0] in THAI_TONE_MARKS:
            return True
        # Rule B: tone mark ติดกัน 2 ตัว
        for i in range(len(th) - 1):
            if th[i] in THAI_TONE_MARKS and th[i+1] in THAI_TONE_MARKS:
                return True
        # Rule C: decode → ถ้าได้ EN word จริงๆ = garbage
        decoded = ''.join(TH_TO_EN_KEY.get(c, '?') for c in th)
        if '?' not in decoded:
            letters  = [c for c in decoded if c.isalpha()]
            specials = [c for c in decoded if c in TH_SPECIAL_KEYS]
            if len(letters) >= MIN_CHARS and len(specials) == 0:
                vowels = sum(1 for c in letters if c in EN_VOWELS)
                if vowels / len(letters) >= 0.15:
                    return True
        # Rule D: ใช้ PyThaiNLP ตรวจว่าเป็นคำไทยจริงไหม
        try:
            from pythainlp.tokenize import word_tokenize
            text = ''.join(th)
            words = word_tokenize(text, keep_whitespace=False)
            known = [w for w in words if len(w) > 1]
            if len(known) == 0:
                return True   # ไม่มีคำไทยจริงเลย = garbage
        except ImportError:
            pass   # ไม่มี pythainlp → ข้ามได้

    # ── EN garbage (Thai Kedmanee typed in EN mode) ──────────
    # นับ non-Thai printable chars ทั้งหมด รวม ;' ที่ใช้ใน Thai Kedmanee
    non_th = [c for c in chars
              if not is_thai(c) and c.isprintable() and ord(c) > 32]
    en_letters = [c for c in non_th if c.isalpha()]

    if len(non_th) >= MIN_CHARS and len(non_th) > len(th):
        vowels = sum(1 for c in en_letters if c in EN_VOWELS)
        denom  = len(en_letters) if en_letters else 1
        ratio  = vowels / denom
        # Rule E: ไม่มี vowel เลย
        if ratio < 0.08:
            return True
        # Rule F: มี ;/' (Thai ว/ง keys) + vowel ต่ำ
        has_thai_special = any(c in (';', "'") for c in non_th)
        if has_thai_special and ratio <= 0.20:
            return True

    return False


# ── NSObject observer สำหรับรับ notification ทันทีที่สลับ input source ──
class _SourceObserver(NSObject):
    """รับ notification com.apple.Carbon.TISNotifySelectedKeyboardInputSourceChanged"""
    def initWithCallback_(self, callback):
        self = super().init()
        if self is None:
            return None
        self._callback = callback
        return self

    def inputSourceChanged_(self, notification):
        try:
            self._callback()
        except Exception as e:
            print(f"[SourceObserver error] {e}")


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
        self.debug_item  = rumps.MenuItem("🐛 Debug: แสดง buffer", callback=self.show_buffer)
        self.access_item = rumps.MenuItem("⚙️ วิธีให้สิทธิ์ Accessibility", callback=self.open_accessibility)
        quit_item        = rumps.MenuItem("❌ ออก", callback=rumps.quit_application)

        self.menu = [
            self.lang_item,
            None,
            self.mon_item,
            self.sound_item,
            self.timer_item,
            None,
            self.debug_item,
            self.access_item,
            None,
            quit_item,
        ]
        self._update_lang_item()

        # ── ลงทะเบียน NSDistributedNotificationCenter สำหรับ input source change ──
        self._observer = _SourceObserver.alloc().initWithCallback_(self._on_source_changed)
        nc = NSDistributedNotificationCenter.defaultCenter()
        nc.addObserver_selector_name_object_(
            self._observer,
            "inputSourceChanged:",
            "com.apple.Carbon.TISNotifySelectedKeyboardInputSourceChanged",
            None,
        )

        # ── thread สำหรับ idle timer เท่านั้น ──
        t = threading.Thread(target=self._idle_loop, daemon=True)
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
        try:
            chars = event.characters()
            if not chars:
                return
            for c in chars:
                if c in (' ', '\r', '\n', '\t', '.', ',', '!', '?', ':', ';'):
                    self._check_and_alert()
                    self.recent_chars.clear()
                elif c.isprintable() and ord(c) > 31:
                    self.recent_chars.append(c)
                    if len(self.recent_chars) >= MIN_CHARS:
                        self._check_and_alert()
        except Exception as e:
            print(f"[key handler error] {e}")

    def _check_and_alert(self):
        if len(self.recent_chars) < MIN_CHARS:
            return
        if not is_garbage(self.recent_chars):
            return
        now = time.time()
        if now - self.last_alert_time < GARBAGE_COOLDOWN:
            self.recent_chars.clear()   # clear อยู่ดีแม้ยังอยู่ใน cooldown
            return
        self.last_alert_time = now
        self.recent_chars.clear()
        threading.Thread(target=self._trigger_garbage_alert, daemon=True).start()

    def _trigger_garbage_alert(self):
        ts = time.strftime('%H:%M:%S')
        print(f"[ALERT {ts}] garbage — lang={self.current_lang}")

        # เปลี่ยน icon ชั่วคราวเพื่อให้เห็นใน menu bar
        orig = self.title
        self.title = "⚠️"
        threading.Timer(1.5, lambda: setattr(self, 'title', orig)).start()

        # เสียง — Popen ไม่ block
        if self.sound_enabled:
            play_sound(SOUND_GARBAGE)
            time.sleep(0.35)
            play_sound(SOUND_GARBAGE)

        # notification — Popen ไม่ block (ไม่รอ response)
        subprocess.Popen(
            ["osascript", "-e",
             'display notification "ตรวจพบพิมพ์ผิดภาษา — กด Caps Lock ด่วน!" '
             'with title "⚠️ ลืมเปลี่ยนภาษา!"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    # ── callback ทันทีเมื่อ input source เปลี่ยน (event-driven) ─────
    def _on_source_changed(self):
        lang = get_input_source()
        if lang == self.current_lang:
            return
        self.current_lang     = lang
        self.last_switch_time = time.time()
        self.recent_chars.clear()
        # Grace period: ไม่ alert 0.6 วิ หลังสลับ เพื่อให้ buffer สะอาด
        self.last_alert_time  = time.time() + 0.6
        self._update_icon()
        self._update_lang_item()
        print(f"[source] switched → {lang}")
        if self.sound_enabled:
            threading.Thread(
                target=lambda: play_sound(SOUND_SWITCH), daemon=True
            ).start()

    # ── background loop: idle timer เท่านั้น ─────────────────
    def _idle_loop(self):
        while True:
            time.sleep(5)   # ตรวจทุก 5 วิ เพียงพอสำหรับ idle timer
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

    def show_buffer(self, _):
        buf = self.recent_chars if self.recent_chars else ["(ว่าง)"]
        chars_str = " ".join(repr(c) for c in buf)
        unicodes = " ".join(f"U+{ord(c):04X}" for c in self.recent_chars) if self.recent_chars else "-"
        rumps.alert(
            title="🐛 Debug Buffer",
            message=f"monitoring: {self.monitoring}\n\nbuffer: {chars_str}\n\nunicode: {unicodes}\n\ninput: {self.current_lang}"
        )

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
