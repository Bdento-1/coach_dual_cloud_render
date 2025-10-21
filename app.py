# ==========================================================
#  Gatekeeper + Voice Coach Integration (Hybrid-Auto v2.3.1)
#  Author: Pattarapon R. (คุณท่าน) — System GPT-5 Safe Mode
# ==========================================================

from flask import Flask, request, jsonify
import os, time, json, base64, hashlib, requests
from openai import OpenAI

# -------------------------------------
# ✅ INITIAL SETUP
# -------------------------------------
app = Flask(__name__)

# โหลดค่า Environment ปลอดภัยจาก Render หรือ Local
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_ORG = os.getenv("OPENAI_ORG")
OPENAI_HTTP_TIMEOUT = int(os.getenv("OPENAI_HTTP_TIMEOUT", "70"))
OPENAI_MAX_RETRIES = int(os.getenv("OPENAI_MAX_RETRIES", "3"))

client = OpenAI(api_key=OPENAI_API_KEY, organization=OPENAI_ORG)

# -------------------------------------
# ✅ SYSTEM PROMPT: ป้องกันการให้คำแนะนำการเงิน
# -------------------------------------
SYSTEM_PROMPT = (
    "You are an analytical trading assistant. "
    "Describe the market structure objectively using neutral tone. "
    "Do not provide buy/sell/hold advice. "
    "Respond in Thai. Add disclaimer: 'ข้อมูลนี้เพื่อการศึกษาเท่านั้น.'"
)

# -------------------------------------
# ✅ GPT-5 SAFE REQUEST FUNCTION
# -------------------------------------
def ask_gpt(messages):
    wait = 0.5
    for i in range(OPENAI_MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model="gpt-5",
                messages=messages,
                timeout=OPENAI_HTTP_TIMEOUT,
            )
            return resp.choices[0].message.content
        except Exception as e:
            print(f"⚠️ GPT error (try {i+1}): {e}")
            if i == OPENAI_MAX_RETRIES - 1:
                raise
            time.sleep(wait)
            wait *= 2

# -------------------------------------
# ✅ CORE ANALYSIS FUNCTION
# -------------------------------------
def coach_text(symbol, tf, close, volume):
    user_prompt = (
        f"วิเคราะห์หุ้น {symbol} บนกรอบเวลา {tf} "
        f"ราคาปิด {close} ปริมาณ {volume}. "
        "อธิบายโครงสร้างคลื่นและแรงซื้อขายอย่างเป็นกลาง"
    )

    try:
        text = ask_gpt([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ])
    except Exception as e:
        print(f"❌ GPT fallback: {e}")
        text = (
            f"ระบบวิเคราะห์ช้าชั่วคราว: {symbol} {tf} ราคาปิด {close}. "
            "ข้อมูลนี้เพื่อการศึกษาเท่านั้น."
        )

    # ✅ กันคำต้องห้าม
    banned = ["buy", "sell", "entry", "exit", "short", "long"]
    if any(b in text.lower() for b in banned):
        text = "ข้อมูลนี้เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำทางการเงิน."
    return text

# -------------------------------------
# ✅ TEXT TO SPEECH (ALLOY)
# -------------------------------------
def tts_alloy(text):
    try:
        r = client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=text,
        )
        audio_bytes = r.read()
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        return {"ok": True, "audio_b64": audio_b64, "text": text}
    except Exception as e:
        print(f"❌ Voice error: {e}")
        return {"ok": False, "text": text, "audio_b64": None}

# -------------------------------------
# ✅ MAIN ENDPOINT
# -------------------------------------
@app.route("/coach_dual", methods=["POST"])
def coach_dual():
    try:
        token = request.args.get("token", "")
        if token != os.getenv("WEBHOOK_TOKEN", "kunthan-voice-01"):
            return jsonify({"ok": False, "error": "unauthorized"}), 403

        data = request.get_json(force=True)
        symbol = data.get("symbol", "?")
        tf = data.get("tf", "?")
        close = data.get("close", "?")
        volume = data.get("volume", "?")

        safety_id = hashlib.sha256(
            f"{symbol}{tf}{close}{volume}".encode()
        ).hexdigest()[:16]

        text = coach_text(symbol, tf, close, volume)
        result = tts_alloy(text)
        result["safety_id"] = safety_id
        return jsonify(result)

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# -------------------------------------
# ✅ HEALTH CHECK
# -------------------------------------
@app.route("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "model": "gpt-5",
        "voice": "alloy",
        "timeout": OPENAI_HTTP_TIMEOUT,
        "retries": OPENAI_MAX_RETRIES
    })

# -------------------------------------
# ✅ ENTRY POINT
# -------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"🚀 Voice Coach Server running on port {port}")
    app.run(host="0.0.0.0", port=port)
