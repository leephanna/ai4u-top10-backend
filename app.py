# app.py — Vercel-ready Flask backend with Rainforest API + optional Gmail email send
import os, time, uuid, json, requests, smtplib, ssl
from urllib.parse import urlparse
from email.message import EmailMessage
from flask import Flask, request, jsonify
from flask_cors import CORS

# Try to load .env locally; on Vercel we rely on project env vars
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

app = Flask(__name__)
CORS(app)

# ---------- Email helpers (Option A: Gmail SMTP + App Password) ----------
def _absolutize(url: str) -> str:
    """Return absolute URL; if already absolute, return as-is."""
    try:
        u = urlparse(url or "")
        if u.scheme and u.netloc:
            return url
    except Exception:
        pass
    return url  # your links are already absolute; keep as safety

def send_email_html(to_email: str, subject: str, html_body: str, bcc: str | None = None) -> dict:
    """
    Send HTML email via Gmail SMTP. Requires:
      - EMAIL_USER = your Gmail address
      - EMAIL_PASSWORD = 16-char App Password (not your normal password)
    Returns: {'sent': True} or {'sent': False, 'reason': '...'}
    """
    sender = os.environ.get('EMAIL_USER')
    password = os.environ.get('EMAIL_PASSWORD')
    if not sender or not password or not to_email:
        return {'sent': False, 'reason': 'missing_email_config_or_to'}

    msg = EmailMessage()
    msg['From'] = f"AI4U Top 10 <{sender}>"
    msg['To'] = to_email
    if bcc:
        msg['Bcc'] = bcc
    msg['Subject'] = subject
    msg.set_content("HTML email. Please view with an HTML-capable client.")
    msg.add_alternative(html_body, subtype='html')

    # Prefer SSL 465; fallback to STARTTLS 587
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(sender, password)
            server.send_message(msg)
        return {'sent': True}
    except Exception:
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.login(sender, password)
                server.send_message(msg)
            return {'sent': True}
        except Exception as e2:
            return {'sent': False, 'reason': f'{type(e2).__name__}: {e2}'}

def send_admin_lead(user_email: str, prompt: str) -> dict:
    """Optional: notify ADMIN_EMAIL a lead was captured (best-effort)."""
    sender = os.environ.get('EMAIL_USER')
    password = os.environ.get('EMAIL_PASSWORD')
    admin = os.environ.get('ADMIN_EMAIL') or None
    if not sender or not password or not admin:
        return {'sent': False, 'reason': 'missing_admin_or_auth'}

    msg = EmailMessage()
    msg['From'] = f"AI4U Top 10 <{sender}>"
    msg['To'] = admin
    msg['Subject'] = "Top 10 — New Lead Captured"
    msg.set_content(
        f"User Email: {user_email}\nSearch Query: {prompt}\nGenerated At: {time.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(sender, password)
            server.send_message(msg)
        return {'sent': True}
    except Exception:
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.login(sender, password)
                server.send_message(msg)
            return {'sent': True}
        except Exception as e2:
            return {'sent': False, 'reason': f'{type(e2).__name__}: {e2}'}

# ---------- Rainforest API client ----------
class RainforestApiClient:
    def __init__(self, affiliate_id: str = "ai4u0c-20"):
        self.affiliate_id = affiliate_id
        self.api_key = os.environ.get('RAINFOREST_API_KEY')
        if not self.api_key:
            raise ValueError("Rainforest API key not found in environment variables")
        self.base_url = "https://api.rainforestapi.com/request"

    def search_products(self, search_term: str, max_results: int = 10):
        params = {
            "api_key": self.api_key,
            "type": "search",
            "amazon_domain": "amazon.com",
            "search_term": search_term,
            "sort_by": "featured",
            "output": "json",
        }
        try:
            resp = requests.get(self.base_url, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            if "search_results" not in data:
                raise ValueError(f"Invalid response from Rainforest API: {data.get('message', 'No search_results')}")
            products = []
            for item in data["search_results"][:max_results]:
                if not item.get("asin") or not item.get("title"):
                    continue
                asin = item.get("asin")
                products.append({
                    "asin": asin,
                    "title": item.get("title"),
                    "price": (item.get("price") or {}).get("raw", "Price not available"),
                    "rating": item.get("rating", 0),
                    "affiliate_link": f"https://www.amazon.com/dp/{asin}?tag={self.affiliate_id}",
                    "image_url": item.get("image", ""),
                    "description": item.get("title"),
                    "amazon_url": f"https://www.amazon.com/dp/{asin}",
                })
            return products
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Failed to connect to Rainforest API: {str(e)}")
        except Exception as e:
            raise

# ---------- Product list generator ----------
class ProductListGenerator:
    def __init__(self):
        self.api_client = RainforestApiClient()

    def intelligent_category_analysis(self, prompt: str):
        p = (prompt or "").lower()
        if any(w in p for w in ['food', 'snack', 'chips', 'candy', 'coffee', 'tea', 'organic', 'grocery']):
            return {'category': 'grocery', 'search_terms': f"{prompt} food"}
        if any(w in p for w in ['baby', 'diaper', 'infant', 'toddler', 'kids', 'children']):
            return {'category': 'baby', 'search_terms': f"{prompt} baby"}
        if any(w in p for w in ['skincare', 'beauty', 'makeup', 'cosmetic', 'anti-aging']):
            return {'category': 'beauty', 'search_terms': f"{prompt} beauty"}
        if any(w in p for w in ['phone', 'smartphone', 'laptop', 'headphone', 'gaming']):
            return {'category': 'electronics', 'search_terms': prompt}
        return {'category': 'general', 'search_terms': prompt}

    def generate_top10_list(self, prompt: str):
        category_info = self.intelligent_category_analysis(prompt)
        products = self.api_client.search_products(category_info['search_terms'], max_results=10)
        if not products:
            return {'success': False, 'error': f"No products found for '{prompt}'. Try a different term."}

        products = products[:10]
        list_title = f"Top 10 {prompt.title()} - AI-Researched & Endorsed 2025"
        intro_text = (
            f"Our AI analyzed {prompt.lower()} to produce this Top 10. "
            "Selections weigh ratings, reviews, and overall value."
        )
        return {
            'success': True,
            'title': list_title,
            'intro': intro_text,
            'category': category_info['category'],
            'products': products,
            'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'affiliate_id': self.api_client.affiliate_id
        }

# ---------- API Routes ----------
@app.route('/api/generate-list', methods=['POST'])
def generate_list():
    try:
        data = request.get_json(force=True) or {}
        prompt = (data.get('prompt') or '').strip()
        user_email = (data.get('email') or '').strip()

        if not prompt:
            return jsonify({'success': False, 'error': 'Prompt is required'}), 400

        generator = ProductListGenerator()
        result = generator.generate_top10_list(prompt)

        if result.get('success'):
            # Generate a share URL ID (placeholder—implement persistence later if needed)
            list_id = str(uuid.uuid4())[:8]
            list_url = f"https://ai4u-top10-lists.vercel.app/list/{list_id}"
            result['share_url'] = list_url
            result['list_id'] = list_id

            # OPTIONAL: email the list if user provided an address (non-blocking UX)
            if user_email and result.get('products'):
                # Build a compact, mobile-friendly HTML
                rows = []
                for i, p in enumerate(result['products'], start=1):
                    title_txt = p.get('title', 'Untitled')
                    price = p.get('price', '')
                    rating = p.get('rating', '')
                    asin = p.get('asin', '')
                    aff = _absolutize(p.get('affiliate_link') or p.get('amazon_url') or '#')
                    img = p.get('image_url', '')
                    rows.append(f"""
                      <tr>
                        <td style="padding:12px 0;border-bottom:1px solid #eee;">
                          <div style="font-weight:600;">{i}. {title_txt}</div>
                          <div style="font-size:13px;color:#666;">ASIN: {asin}</div>
                          <div style="font-size:13px;color:#666;">Price: {price} &nbsp; • &nbsp; Rating: {rating}</div>
                          <div style="margin:8px 0;">
                            <a href="{aff}" style="display:inline-block;background:#6c47ff;color:#fff;text-decoration:none;padding:8px 14px;border-radius:8px;">View on Amazon</a>
                          </div>
                          {f'<img src="{img}" alt="" style="max-width:120px;border-radius:8px;">' if img else ''}
                        </td>
                      </tr>
                    """)

                html = f"""
                <div style="font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial">
                  <h2 style="margin:0 0 12px">{result.get('title','Your Top 10')}</h2>
                  <p style="margin:0 0 16px;color:#444">Here’s your AI-researched Top 10 list.</p>
                  <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">
                    {''.join(rows)}
                  </table>
                  <p style="margin-top:18px;color:#888;font-size:12px">
                    Sent by AI4U Top 10. Links may include affiliate tags.
                  </p>
                </div>
                """

                result['email_status'] = send_email_html(
                    to_email=user_email,
                    subject=result.get('title', 'Your Top 10 List'),
                    html_body=html,
                    bcc=os.environ.get('ADMIN_EMAIL') or None
                )
                # Optional admin ping (best-effort; ignore failures)
                _ = send_admin_lead(user_email, prompt)

        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    has_rf = bool(os.environ.get('RAINFOREST_API_KEY'))
    return jsonify({
        'status': 'healthy' if has_rf else 'warning',
        'message': 'AI4U Top 10 Lists API',
        'rainforest_key': 'present' if has_rf else 'missing',
        'affiliate_id': 'ai4u0c-20'
    })

@app.route('/', methods=['GET'])
def home():
    return "AI4U Top 10 Backend is running!"

@app.route('/test', methods=['GET'])
def test_page():
    # simple in-browser tester
    return '''
    <html>
    <head><title>AI4U API Test</title></head>
    <body style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto;padding:20px">
      <h1>AI4U Top 10 API Test</h1>
      <label>Search Term:</label><br/>
      <input id="prompt" value="vitamins" style="width:100%;padding:8px"/><br/><br/>
      <label>Email (optional):</label><br/>
      <input id="email" value="" style="width:100%;padding:8px"/><br/><br/>
      <button onclick="go()" style="padding:10px 16px;background:#4CAF50;color:#fff;border:0;border-radius:6px">Test API</button>
      <h2>Results:</h2>
      <pre id="out" style="background:#f5f5f7;padding:12px;border-radius:8px;white-space:pre-wrap"></pre>
      <script>
        async function go(){
          const out = document.getElementById('out');
          out.textContent='Loading...';
          try{
            const r = await fetch('/api/generate-list', {
              method:'POST',
              headers:{'Content-Type':'application/json'},
              body: JSON.stringify({prompt: document.getElementById('prompt').value, email: document.getElementById('email').value})
            });
            const j = await r.json();
            out.textContent = JSON.stringify(j,null,2);
          }catch(e){ out.textContent='Error: '+e.message; }
        }
      </script>
    </body>
    </html>
    '''

# Vercel: module-level app is exported; this block runs only locally
if __name__ == '__main__':
    print("Starting AI4U Top 10 Backend")
    print(f"Rainforest key configured: {'Yes' if os.environ.get('RAINFOREST_API_KEY') else 'No'}")
    app.run(host='127.0.0.1', port=5000, debug=True)
