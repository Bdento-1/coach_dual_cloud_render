# app.py  — Gatekeeper + Coach (v2.3.1 cloud, dual-voice, fast-mode)
from flask import Flask, request, jsonify
import os, time, base64, hashlib

from openai import OpenAI

app = Flask(__name__)

# ===== ENV =====
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY", "")
OPENAI_ORG       = os.getenv("OPENAI_ORG")  # optional
OPENAI_TIMEOUT   = int(os.getenv("OPENAI_TIMEOUT", "70"))    # default slow-safe
OPENAI_RETRIES   = int(os.getenv("OPENAI_MAX_RETRIES", "3"))
WEBHOOK_TOKEN    = os.getenv("WEBHOOK_TOKEN", "kunthan-voice-01")

# voices & tts model
TTS_MODEL        = os.getenv("TTS_MODEL", "gpt-4o-mini-tts")
GATE_VOICE       = os.getenv("GATE_VOICE", "alloy")  # แจ้งเตือน/analytic
COACH_VOICE      = os.getenv("COACH_VOICE", "nova")  # สรุปยาว/ธรรมชาติ

# safety id namespace
SAFETY_NAMESPACE = os.getenv("SAFETY_NAMESPACE", "gatekeeper-v231")

# allowlist voices (กันค่าไม่รองรับ)
_ALLOWED_VOICES = {
    "alloy","echo","fable","onyx","nova","shimmer",
    "coral","verse","ballad","ash","sage","marin","cedar"
}

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing.")

client_kwargs = {"api_key": OPENAI_API_KEY}
if OPENAI_ORG:
    client_kwargs["organization"] = OPENAI_ORG
client = OpenAI(**client_kwargs)

# ===== System prompt (ไทย) =====
SYSTEM_PROMPT_FULL = (
    "คุณเป็นผู้ช่วยวิเคราะห์โครงสร้างราคาที่เป็นกลาง (Analytical). "
    "หน้าที่: อธิบาย Market Structure, BOS/CHOCH, แนวรับ-แนวต้านจากสวิง, "
    "บริบทวอลุ่ม/สภาพคล่อง, ความผันผวน. "
    "ห้ามให้คำแนะนำทางการเงิน (ห้ามระบุ buy/sell/entry/exit/TP/SL). "
    "สรุปชัด กระชับ โทนมืออาชีพ. ลงท้ายด้วยข้อความ: "
    "'ข้อมูลนี้เพื่อการศึกษาเท่านั้น.'"
)

SYSTEM_PROMPT_FAST = (
    "สรุปโครงสร้างราคาแบบย่อ (Objective). "
    "บอกกรอบ/โซนสำคัญ + สถานการณ์ BOS/CHOCH หากมี. "
    "ห้ามคำแนะนำทางการเงิน. ลงท้าย: 'ข้อมูลนี้เพื่อการศึกษาเท่านั้น.'"
)

FINADV_BANNED = (" buy"," sell"," entry"," exit"," long"," short"," tp"," sl")
def _strip_fin_advice(text: str) -> str:
    low = text.lower()
    if any(k in low for k in FINADV_BANNED):
        return "ข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำทางการเงิน."
    return text

def _pick_timeout(fast_flag: bool) -> int:
    # fast=1 → timeouts สั้นลงเพื่อลดค้าง
    return min(OPENAI_TIMEOUT, 40) if fast_flag else OPENAI_TIMEOUT

def _ask_gpt_thai(symbol, tf, close, volume, fast: bool):
    sys_prompt = SYSTEM_PROMPT_FAST if fast else SYSTEM_PROMPT_FULL
    user_prompt = (
        f"วิเคราะห์สินทรัพย์ {symbol} กรอบเวลา {tf} "
        f"(ราคาปิดล่าสุด {close}, ปริมาณ {volume}). "
        "ให้โครงสร้างราคา/วอลุ่ม/สภาพคล่อง/กรอบอ้างอิง. ภาษาไทย."
    )
    wait = 0.6
    last_err = None
    for _ in range(OPENAI_RETRIES):
        try:
            r = client.chat.completions.create(
                model="gpt-5",
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                timeout=_pick_timeout(fast),
            )
            content = r.choices[0].message.content or ""
            return _strip_fin_advice(content.strip())
        except Exception as e:
            last_err = e
            time.sleep(wait)
            wait = min(wait * 1.8, 4.0)
    raise last_err

def _safe_voice(name: str, default_: str) -> str:
    name = (name or default_).strip().lower()
    return name if name in _ALLOWED_VOICES else default_

def _tts(text: str, voice: str):
    """เรียก TTS → base64(mp3) | ไม่ throw: คืน ok=False เมื่อพัง"""
    use_voice = _safe_voice(voice, GATE_VOICE)
    try:
        r = client.audio.speech.create(
            model=TTS_MODEL,
            voice=use_voice,
            input=text,
        )
        audio_bytes = r.read()
        return {
            "ok": True,
            "text": text,
            "audio_b64": base64.b64encode(audio_bytes).decode("utf-8"),
            "audio_mime": "audio/mpeg",
            "voice": use_voice,
        }
    except Exception as e:
        return {"ok": False, "text": text, "error": f"tts_failed: {e}", "voice": use_voice}

@app.route("/coach_dual", methods=["POST"])
def coach_dual():
    try:
        # auth
        token = request.args.get("token", "")
        if token != WEBHOOK_TOKEN:
            # รองรับ header สำรอง X-Webhook-Token เช่นกรณี TradingView
            header_token = request.headers.get("X-Webhook-Token", "")
            if header_token != WEBHOOK_TOKEN:
                return jsonify({"ok": False, "error": "unauthorized"}), 403

        data   = request.get_json(force=True) or {}
        fast   = str(request.args.get("fast", data.get("fast", ""))).strip() in ("1","true","True")
        mode   = str(request.args.get("mode", data.get("mode","")) or "").lower().strip()
        # mode: "", "gate", "coach", "both"  (ว่าง=both)

        symbol = data.get("symbol", "?")
        tf     = data.get("tf", "?")
        close  = data.get("close", "?")
        volume = data.get("volume", "?")

        # safety id
        safety_src = f"{SAFETY_NAMESPACE}|{symbol}|{tf}|{close}|{volume}"
        safety_id  = hashlib.sha256(safety_src.encode()).hexdigest()[:16]

        # 1) สร้างข้อความสรุป (ไทย)
        try:
            text = data.get("text") or _ask_gpt_thai(symbol, tf, close, volume, fast=fast)
        except Exception:
            text = (
                f"สรุป ({tf}): ปิด {close}, วอลุ่ม {volume}. "
                "ระบบหลักหน่วง ใช้สรุปย่อชั่วคราว. ข้อมูลนี้เพื่อการศึกษาเท่านั้น."
            )

        # 2) เลือกชั้นเสียง
        # - gate: alloy แจ้งสั้น analytic
        # - coach: nova สรุปยาว
        # - both/blank: สร้างเสียง coach ตามดีฟอลต์
        resp = {"ok": True, "safety_id": safety_id, "text": text}

        chosen = (mode or "both")
        if chosen in ("gate", "both"):
            t_gate = _tts(text, GATE_VOICE)
            resp["gate"] = t_gate

        if chosen in ("coach", "both"):
            t_coach = _tts(text, COACH_VOICE)
            resp["coach"] = t_coach

        # หากทั้งสองชั้นล้มเหลว ให้ ok=False แต่ยังส่งข้อความคืน
        gate_ok  = resp.get("gate",{}).get("ok", False)
        coach_ok = resp.get("coach",{}).get("ok", False)
        resp["ok"] = bool(gate_ok or coach_ok)

        # inline shortcut (เดิม client ที่คาดหวัง audio_b64 เดียว)
        if coach_ok:
            resp["audio_b64"]  = resp["coach"]["audio_b64"]
            resp["audio_mime"] = resp["coach"]["audio_mime"]
        elif gate_ok:
            resp["audio_b64"]  = resp["gate"]["audio_b64"]
            resp["audio_mime"] = resp["gate"]["audio_mime"]

        return jsonify(resp), 200
    except Exception as e:
        return jsonify({"ok": False, "error": f"handler_failed: {e}"}), 500

@app.route("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "model": "gpt-5",
        "tts_model": TTS_MODEL,
        "gate_voice": GATE_VOICE,
        "coach_voice": COACH_VOICE,
        "retries": OPENAI_RETRIES,
        "timeout": OPENAI_TIMEOUT
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
