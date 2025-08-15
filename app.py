import os
import re
import time
import json
import uuid
import logging
from typing import Dict, List, Optional

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# ---------------------------
# Boot / Config
# ---------------------------
load_dotenv()  # no-op on Vercel, works locally

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Logging (Heroku/Vercel friendly)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("ai4u-backend")

# Env (required)
RAINFOREST_API_KEY = os.getenv("RAINFOREST_API_KEY", "")
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("EMAIL_USER", "") or os.getenv("FROM_EMAIL", "")  # supports your existing var
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
AFFILIATE_ID = os.getenv("AFFILIATE_ID", "ai4u0c-20")  # your tag default

# Hard caps / timeouts
REQ_TIMEOUT = 15  # seconds
MAX_PRODUCTS = 10

# Basic email validation
EMAIL_RE = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)

# ---------------------------
# Utils
# ---------------------------

def safe_email(s: str) -> bool:
    return bool(s and EMAIL_RE.match(s))

def rainforests_search(search_term: str) -> List[Dict]:
    """Search RainforestAPI. Returns a simplified product list."""
    if not RAINFOREST_API_KEY:
        raise RuntimeError("Rainforest API key missing on server")

    params = {
        "api_key": RAINFOREST_API_KEY,
        "type": "search",
        "amazon_domain": "amazon.com",
        "search_term": search_term,
        "sort_by": "featured",
    }

    url = "https://api.rainforestapi.com/request"
    log.info("Rainforest request: %s", params)

    r = requests.get(url, params=params, timeout=REQ_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"Rainforest HTTP {r.status_code}: {r.text[:200]}")

    data = r.json()
    results = data.get("search_results", []) or []
    products: List[Dict] = []

    for p in results[:MAX_PRODUCTS]:
        asin = p.get("asin")
        if not asin:
            continue
        title = p.get("title", "")
        price = (p.get("price", {}) or {}).get("raw", "")
        rating = p.get("rating", 0)
        image = p.get("image") or ""
        snippet = p.get("snippet") or ""

        # affiliate link
        affiliate = f"https://www.amazon.com/dp/{asin}?tag={AFFILIATE_ID}"

        products.append({
            "asin": asin,
            "title": title,
            "price": price,
            "rating": rating,
            "image": image,
            "snippet": snippet,
            "affiliate_link": affiliate,
            "amazon_url": f"https://www.amazon.com/dp/{asin}",
        })

    if not products:
        raise RuntimeError("No products found for that query")

    return products

def render_email_html(title: str, products: List[Dict]) -> str:
    """Very lightweight inline HTML email."""
    rows = []
    for i, p in enumerate(products, start=1):
        rows.append(f"""
        <tr>
          <td style="padding:16px 0;border-bottom:1px solid #eee;">
            <div style="font:700 16px/1.3 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial">
              {i}. {p.get('title','')}
            </div>
            <div style="margin:8px 0">
              <img src="{p.get('image','')}" alt="" width="220" style="border-radius:8px;display:block"/>
            </div>
            <div style="color:#666;font:14px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial">
              ASIN: {p.get('asin','')} &nbsp;&nbsp; Price: {p.get('price','')} &nbsp;&nbsp; Rating: {p.get('rating',0)}
            </div>
            <div style="margin:10px 0">
              <a href="{p.get('affiliate_link','')}" target="_blank"
                 style="background:#6C5CE7;color:#fff;text-decoration:none;padding:10px 14px;border-radius:8px;display:inline-block">
                 View on Amazon
              </a>
            </div>
          </td>
        </tr>
        """)

    body = "\n".join(rows)
    return f"""
<!doctype html>
<html>
  <body style="margin:0;padding:24px;background:#fafafa;">
    <center>
      <table width="640" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:14px;padding:24px">
        <tr>
          <td style="font:800 22px/1.2 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial">
            {title}
          </td>
        </tr>
        <tr><td style="height:12px"></td></tr>
        <tr>
          <td style="color:#777;font:14px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial">
            Here’s your AI-researched Top 10 list.
          </td>
        </tr>
        <tr><td style="height:16px"></td></tr>
        {body}
      </table>
      <div style="color:#999;font:12px/1.4 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial;margin-top:16px">
        © AI4U — Automated Top 10 Lists
      </div>
    </center>
  </body>
</html>
    """.strip()

def send_with_resend(to_email: str, subject: str, html_body: str) -> Dict:
    """Send email through Resend API with simple retry."""
    if not (RESEND_API_KEY and FROM_EMAIL):
        raise RuntimeError("Email sending not configured (RESEND_API_KEY/FROM_EMAIL)")

    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "from": FROM_EMAIL,       # e.g. "AI4U Top 10 <top10@ai4utech.com>"
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    }

    # small retry for transient errors
    for attempt in range(1, 4):
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=REQ_TIMEOUT)
        if resp.status_code in (200, 202):
            return resp.json()
        # transient? try again
        if resp.status_code >= 500:
            time.sleep(0.8 * attempt)
            continue
        # not transient
        raise RuntimeError(f"Resend error {resp.status_code}: {resp.text[:200]}")

    raise RuntimeError("Resend failed after retries")

def notify_admin_lead(query: str, user_email: str, req_id: str) -> None:
    """Fire-and-forget admin notification (best-effort)."""
    if not (RESEND_API_KEY and ADMIN_EMAIL and FROM_EMAIL):
        return
    try:
        html = f"""
        <p>New lead captured.</p>
        <p><b>User Email:</b> {user_email}</p>
        <p><b>Search Query:</b> {query}</p>
        <p><b>Request ID:</b> {req_id}</p>
        <p><small>Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}</small></p>
        """
        send_with_resend(ADMIN_EMAIL, "Top 10 — New Lead Captured", html)
    except Exception as e:
        log.warning("Admin notify failed: %s", e)

# ---------------------------
# HTTP
# ---------------------------

@app.get("/")
def home():
    return "<h2>AI4U Top 10 Backend is running!</h2>"

@app.get("/api/health")
def health():
    ok = bool(RAINFOREST_API_KEY)
    return jsonify({"ok": ok})

@app.post("/api/generate-list")
def generate_list():
    req_id = str(uuid.uuid4())[:8]
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"success": False, "error": "Invalid JSON body"}), 400

    prompt = (data.get("prompt") or "").strip()
    user_email = (data.get("email") or "").strip()

    if not prompt:
        return jsonify({"success": False, "error": "Missing prompt"}), 400

    log.info("[%s] generate-list: q=%r email=%s", req_id, prompt, user_email or "-")

    try:
        products = rainforests_search(prompt)
    except Exception as e:
        log.error("[%s] rainforest error: %s", req_id, e)
        return jsonify({"success": False, "error": f"Data provider error: {e}"}), 502

    title = f"Top 10 {prompt.title()} - AI-Researched & Endorsed 2025"
    email_html = render_email_html(title, products)

    # Optional email to user
    sent = None
    if user_email:
        if not safe_email(user_email):
            return jsonify({"success": False, "error": "Invalid email"}), 400
        try:
            sent = send_with_resend(user_email, title, email_html)
            notify_admin_lead(prompt, user_email, req_id)
        except Exception as e:
            # Do not fail the whole request if email send has a temporary issue
            log.warning("[%s] email send failed: %s", req_id, e)

    return jsonify({
        "success": True,
        "title": title,
        "affiliate_id": AFFILIATE_ID,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "email": {"attempted": bool(user_email), "provider_response": sent},
        "products": products,
    })

# Simple HTML preview (for debugging templates from a browser)
@app.get("/api/preview-email")
def preview_email():
    q = request.args.get("q", "beer")
    try:
        products = rainforests_search(q)
        html = render_email_html(f"Top 10 {q.title()} - Preview", products)
        return html
    except Exception as e:
        return f"<pre>Error: {e}</pre>", 500

# Local dev
if __name__ == "__main__":
    app.run(debug=True)
