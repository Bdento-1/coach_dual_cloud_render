#!/usr/bin/env python3
from flask import Flask, request, jsonify, Response
import os, time, base64, hashlib, re
from openai import OpenAI

app = Flask(__name__)

# ===== ENV =====
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY", "")
OPENAI_ORG       = os.getenv("OPENAI_ORG")
OPENAI_TIMEOUT   = int(os.getenv("OPENAI_TIMEOUT", "90"))
OPENAI_RETRIES   = int(os.getenv("OPENAI_MAX_RETRIES", "4"))
WEBHOOK_TOKEN    = os.getenv("WEBHOOK_TOKEN", "kunthan-voice-01")

TTS_MODEL        = os.getenv("TTS_MODEL", "gpt-4o-mini-tts")
VOICE_DEFAULT    = os.getenv("VOICE", "nova")

COACH_VOICE      = os.getenv("COACH_VOICE", "nova")
GATE_VOICE       = os.getenv("GATE_VOICE",  "verse")

# global fallback
VOICE_RATE_FALL  = float(os.getenv("VOICE_RATE", "0.92"))
# NEW: rate แยกตามบทบาท
VOICE_RATE_COACH = float(os.getenv("VOICE_RATE_COACH", str(VOICE_RATE_FALL)))
VOICE_RATE_GATE  = float(os.getenv("VOICE_RATE_GATE",  str(max(0.9, VOICE_RATE_FALL))))

VOICE_PITCH      = os.getenv("VOICE_PITCH", "-0.5")
VOICE_PAUSE_MS   = os.getenv("VOICE_PAUSE_MS", "400")
VOICE_LANGUAGE   = os.getenv("VOICE_LANGUAGE", "th")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing.")

client_kwargs = {"api_key": OPENAI_API_KEY}
if OPENAI_ORG:
    client_kwargs["organization"] = OPENAI_ORG
client = OpenAI(**client_kwargs)

SYSTEM_PROMPT = (
    "คุณคือผู้ช่วยวิเคราะห์เชิงโครงสร้างราคาแบบเป็นกลาง (Gatekeeper v2.3.1 Hybrid-Auto). "
    "ให้สรุปภาษาไทยเท่านั้น, ไม่แนะนำซื้อขาย, และพูดด้วยน้ำเสียงธรรมชาติ "
    "ลงท้ายด้วยประโยค: 'ข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำทางการเงิน.'"
)

# ===== Utilities =====
_digit_map = {'0':'ศูนย์','1':'หนึ่ง','2':'สอง','3':'สาม','4':'สี่','5':'ห้า','6':'หก','7':'เจ็ด','8':'แปด','9':'เก้า','.':'จุด'}
def num_to_thai_digits(s: str) -> str:
    return ' '.join(_digit_map.get(ch, ch) for ch in s)

def normalize_thai_numbers(text: str) -> str:
    return re.sub(r'\d+(\.\d+)?', lambda m: num_to_thai_digits(m.group(0)), text)

def pick_voice(payload: dict) -> str:
    explicit = (payload.get("voice") or "").strip().lower()
    if explicit:
        return explicit
    role = (payload.get("role") or "").strip().lower()
    event = str(payload.get("event") or "").lower()
    if role == "gate" or any(k in event for k in ["gate","trap","alert","stop","risk"]):
        return GATE_VOICE
    return COACH_VOICE

def pick_rate(voice_selected: str, payload: dict) -> float:
    """เลือกความเร็วตามบทบาท/เสียง"""
    role = (payload.get("role") or "").strip().lower()
    event = str(payload.get("event") or "").lower()
    if voice_selected == GATE_VOICE or role == "gate" or any(k in event for k in ["gate","trap","alert","stop","risk"]):
        return VOICE_RATE_GATE
    return VOICE_RATE_COACH

def apply_pacing(text: str, rate: float) -> str:
    """
    ถ้า rate < 0.90 ให้เพิ่มจังหวะพักแบบเนียน:
    - เว้นวรรคเพิ่มหลัง , ; : และใส่ '…' หลังวลี
    - แทรก '…' ทุก ~7 คำ (หรือ 5 คำถ้าช้ามาก ≤0.80)
    """
    if rate >= 0.90:
        return text

    # พักหลังเครื่องหมาย
    t = re.sub(r'([,;:])\s*', r'\1  ', text)

    # แทรกพักทุก N คำ
    words = t.split()
    step  = 5 if rate <= 0.80 else 7
    out = []
    for i, w in enumerate(words, 1):
        out.append(w)
        if i % step == 0:
            out.append("…")
    return " ".join(out)

# ===== GPT =====
def ask_gpt(messages):
    wait = 0.5
    last = None
    for _ in range(OPENAI_RETRIES):
        try:
            r = client.chat.completions.create(
                model="gpt-5",
                messages=messages,
                timeout=OPENAI_TIMEOUT,
            )
            return r.choices[0].message.content
        except Exception as e:
            last = e
            time.sleep(wait)
            wait *= 2
    raise last

# ===== Coach text =====
def coach_text(symbol, tf, close, volume, hint=None):
    user_prompt = (
        f"วิเคราะห์หุ้น {symbol} ในกรอบเวลา {tf} "
        f"ราคาปิด {close} ปริมาณ {volume}. "
        f"{hint or 'สรุปไทยล้วน 4–6 บรรทัด ไม่แนะนำซื้อขาย'}"
    )
    try:
        txt = ask_gpt(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": user_prompt}]
        )
    except Exception:
        txt = f"สรุปข้อมูล ({tf}) — ปิด {close}, ปริมาณ {volume}. ระบบหลักช้า ใช้ข้อความย่อแทน."
    if len(txt) > 1200:
        txt = txt[:1200] + "…"

    banned = ("buy","sell","entry","exit","long","short","tp","sl")
    if any(b in txt.lower() for b in banned):
        txt = "ข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำทางการเงิน."
    return txt

# ===== TTS =====
def tts_generate(text: str, voice: str, rate: float):
    """แปลงเลขเป็นไทย + ชะลอจังหวะตาม rate + สร้างเสียง"""
    text_paced = apply_pacing(normalize_thai_numbers(text), rate)
    try:
        r = client.audio.speech.create(
            model=TTS_MODEL,
            voice=voice,
            input=text_paced,
            # หมายเหตุ: ถ้า API รุ่นใหม่รองรับ speed/pitch ให้ต่อเพิ่มที่นี่ได้
            # speed=rate, pitch=float(VOICE_PITCH)
        )
        audio_bytes = r.read()
        return True, text_paced, base64.b64encode(audio_bytes).decode("utf-8")
    except Exception as e:
        return False, f"tts_failed: {e}", None

# ===== ROUTES =====
@app.route("/coach_dual", methods=["POST"])
def coach_dual():
    try:
        token = request.args.get("token", "")
        if token != WEBHOOK_TOKEN:
            return jsonify({"ok": False, "error": "unauthorized"}), 403

        data   = request.get_json(force=True) or {}
        symbol = data.get("symbol", "?")
        tf     = data.get("tf", "?")
        close  = data.get("close", "?")
        volume = data.get("volume", "?")
        hint   = data.get("hint")

        safety_id = hashlib.sha256(f"{symbol}{tf}{close}{volume}".encode()).hexdigest()[:16]

        text   = data.get("text") or coach_text(symbol, tf, close, volume, hint)
        voice  = pick_voice(data) or VOICE_DEFAULT
        rate   = pick_rate(voice, data)
        ok, text_out, b64 = tts_generate(text, voice, rate)

        return jsonify({
            "ok": ok,
            "safety_id": safety_id,
            "voice": voice,
            "rate": rate,
            "text": text_out if ok else text,
            "audio_b64": b64,
            "audio_mime": "audio/mpeg"
        }), (200 if ok else 500)
    except Exception as e:
        return jsonify({"ok": False, "error": f"handler_failed: {e}"}), 500

@app.route("/speak", methods=["POST"])
def speak():
    try:
        data  = request.get_json(force=True) or {}
        text  = data.get("text", "ระบบพร้อมสำหรับเสียงไทยพรีเมียม")
        voice = pick_voice(data) or VOICE_DEFAULT
        rate  = pick_rate(voice, data)
        ok, text_out, b64 = tts_generate(text, voice, rate)
        if not ok:
            return jsonify({"ok": False, "error": text_out}), 500
        return Response(base64.b64decode(b64), mimetype="audio/mpeg")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "tts_model": TTS_MODEL,
        "voice_default": VOICE_DEFAULT,
        "coach_voice": COACH_VOICE,
        "gate_voice": GATE_VOICE,
        "rate_coach": VOICE_RATE_COACH,
        "rate_gate": VOICE_RATE_GATE,
        "retries": OPENAI_RETRIES,
        "timeout": OPENAI_TIMEOUT
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
