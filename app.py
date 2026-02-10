"""
O*NET Occupation Explorer — Web Application
=============================================
Flask web service that wraps the O*NET Explorer CLI tool,
allowing users to search occupations and view interactive
dashboards in the browser.

Deploy to Render with ONET_API_KEY set as an environment variable.
"""

import os
import html as html_lib
from flask import Flask, request, render_template_string, Response

from onet_explorer import (
    search_occupations,
    get_occupation_summary,
    get_occupation_tasks,
    get_occupation_elements,
    get_education_requirements,
    get_job_zone,
    get_hot_technologies,
    get_occupation_industries,
    get_bls_employment_by_state,
    get_bls_employment_by_industry,
    get_bls_national_employment,
    analyze_ai_impact,
    generate_dashboard,
)

app = Flask(__name__)

API_KEY = os.environ.get("ONET_API_KEY", "")
BLS_KEY = os.environ.get("BLS_API_KEY", "")

# ─── Landing / Search Page ────────────────────────────────────────────────────

LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>O*NET Occupation Explorer</title>
<style>
  :root {
    --bg: #0f172a; --surface: #1e293b; --primary: #6366f1;
    --primary-hover: #818cf8; --text: #f1f5f9; --text-secondary: #94a3b8;
    --border: #334155; --accent: #10b981; --danger: #ef4444;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg); color: var(--text); min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
  }
  .container { width: 100%; max-width: 700px; padding: 20px; }
  .hero { text-align: center; margin-bottom: 40px; }
  .hero h1 { font-size: 2.4rem; font-weight: 700; margin-bottom: 12px;
    background: linear-gradient(135deg, var(--primary), var(--accent));
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  .hero p { color: var(--text-secondary); font-size: 1.1rem; line-height: 1.6; max-width: 520px; margin: 0 auto; }
  .search-box {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 16px; padding: 32px; margin-bottom: 24px;
  }
  .search-box label { display: block; font-weight: 600; margin-bottom: 10px; font-size: 0.95rem; }
  .input-row { display: flex; gap: 12px; }
  .input-row input {
    flex: 1; padding: 14px 18px; border-radius: 10px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text); font-size: 1rem; outline: none;
    transition: border-color 0.2s;
  }
  .input-row input:focus { border-color: var(--primary); }
  .input-row button {
    padding: 14px 28px; border-radius: 10px; border: none;
    background: var(--primary); color: #fff; font-size: 1rem; font-weight: 600;
    cursor: pointer; transition: background 0.2s; white-space: nowrap;
  }
  .input-row button:hover { background: var(--primary-hover); }
  .features { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-top: 28px; }
  .feature {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 20px; text-align: center;
  }
  .feature .icon { font-size: 1.8rem; margin-bottom: 8px; }
  .feature h3 { font-size: 0.9rem; margin-bottom: 4px; }
  .feature p { font-size: 0.78rem; color: var(--text-secondary); }

  /* Results list */
  .results-box {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 16px; padding: 32px; margin-bottom: 24px;
  }
  .results-box h2 { font-size: 1.2rem; margin-bottom: 16px; }
  .occ-link {
    display: flex; align-items: center; gap: 14px; padding: 14px 18px;
    border-radius: 10px; background: var(--bg); border: 1px solid var(--border);
    margin-bottom: 10px; text-decoration: none; color: var(--text);
    transition: border-color 0.2s, transform 0.1s;
  }
  .occ-link:hover { border-color: var(--primary); transform: translateX(4px); }
  .occ-code {
    background: var(--primary); color: #fff; padding: 4px 10px;
    border-radius: 6px; font-size: 0.78rem; font-weight: 600; white-space: nowrap;
  }
  .occ-title { font-weight: 500; }
  .error-box {
    background: rgba(239,68,68,0.1); border: 1px solid var(--danger);
    border-radius: 12px; padding: 16px 20px; color: var(--danger);
    margin-bottom: 20px; font-size: 0.95rem;
  }
  .back-link { color: var(--primary); text-decoration: none; font-size: 0.9rem; }
  .back-link:hover { text-decoration: underline; }
  .loading { text-align: center; padding: 40px; color: var(--text-secondary); }
  .loading .spinner {
    width: 40px; height: 40px; border: 3px solid var(--border);
    border-top-color: var(--primary); border-radius: 50%;
    animation: spin 0.8s linear infinite; margin: 0 auto 16px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  footer { text-align: center; margin-top: 40px; color: var(--text-secondary); font-size: 0.8rem; }
  footer a { color: var(--primary); text-decoration: none; }

  /* Loading overlay */
  .loading-overlay {
    display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    background: rgba(15, 23, 42, 0.92); z-index: 9999;
    flex-direction: column; align-items: center; justify-content: center;
  }
  .loading-overlay.active { display: flex; }
  .loading-overlay .pulse-ring {
    width: 80px; height: 80px; border-radius: 50%;
    border: 4px solid transparent; border-top-color: var(--primary); border-right-color: var(--accent);
    animation: spin 1s linear infinite;
    margin-bottom: 28px;
  }
  .loading-overlay h2 {
    font-size: 1.4rem; font-weight: 700; margin-bottom: 10px;
    background: linear-gradient(135deg, var(--primary), var(--accent));
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  .loading-overlay p { color: var(--text-secondary); font-size: 0.95rem; max-width: 400px; text-align: center; line-height: 1.6; }
  .loading-steps { margin-top: 24px; text-align: left; width: 320px; }
  .loading-step {
    display: flex; align-items: center; gap: 10px; padding: 8px 0;
    font-size: 0.85rem; color: var(--text-secondary); transition: color 0.3s;
  }
  .loading-step.active { color: var(--text); }
  .loading-step.done { color: var(--accent); }
  .step-icon { width: 20px; text-align: center; font-size: 0.9rem; }
  .loading-step.active .step-icon::after { content: "⟳"; animation: spin 1s linear infinite; display: inline-block; }
  .loading-step.pending .step-icon::after { content: "○"; }
  .loading-step.done .step-icon::after { content: "✓"; }
  .loading-elapsed { margin-top: 18px; font-size: 0.78rem; color: var(--text-secondary); opacity: 0.6; }
</style>
</head>
<body>
<div class="container">
  <div class="hero">
    <h1>O*NET Occupation Explorer</h1>
    <p>Search any occupation and get an interactive dashboard with tasks, skills, knowledge, abilities, and AI Impact analysis.</p>
  </div>

  {% if error %}
  <div class="error-box">{{ error }}</div>
  {% endif %}

  {% if results is not none %}
  <div class="results-box">
    <a href="/" class="back-link">&larr; New search</a>
    <h2 style="margin-top:12px">{{ results|length }} occupation{{ 's' if results|length != 1 }} found for &ldquo;{{ keyword }}&rdquo;</h2>
    {% for occ in results %}
    <a class="occ-link" href="/dashboard?code={{ occ.code }}">
      <span class="occ-code">{{ occ.code }}</span>
      <span class="occ-title">{{ occ.title }}</span>
    </a>
    {% endfor %}
  </div>
  {% else %}
  <div class="search-box">
    <label for="keyword">Search for an occupation</label>
    <form action="/" method="get">
      <div class="input-row">
        <input type="text" id="keyword" name="q" placeholder="e.g. software developer, nurse, accountant" required autofocus>
        <button type="submit">Search</button>
      </div>
    </form>
  </div>

  <div class="features">
    <div class="feature"><div class="icon">📊</div><h3>Tasks & Skills</h3><p>O*NET importance ratings and rankings</p></div>
    <div class="feature"><div class="icon">🤖</div><h3>AI Impact</h3><p>Automation & augmentation scoring</p></div>
    <div class="feature"><div class="icon">🧠</div><h3>Agent Recommendations</h3><p>AI agents matched to role tasks</p></div>
    <div class="feature"><div class="icon">📈</div><h3>Interactive Charts</h3><p>Chart.js visualizations</p></div>
  </div>
  {% endif %}

  <footer>
    Powered by <a href="https://services.onetcenter.org/" target="_blank">O*NET Web Services</a>
    &middot; <a href="https://github.com/johneparker/onet-explorer" target="_blank">GitHub</a>
  </footer>
</div>

<!-- Loading overlay -->
<div class="loading-overlay" id="loading-overlay">
  <div class="pulse-ring"></div>
  <h2>Building Your Dashboard</h2>
  <p>Analyzing occupation data from O*NET and the Bureau of Labor Statistics. This typically takes 30–60 seconds.</p>
  <div class="loading-steps">
    <div class="loading-step active" id="step-onet"><span class="step-icon"></span> Fetching O*NET occupation data</div>
    <div class="loading-step pending" id="step-skills"><span class="step-icon"></span> Analyzing skills, knowledge &amp; abilities</div>
    <div class="loading-step pending" id="step-industries"><span class="step-icon"></span> Scanning industry employment</div>
    <div class="loading-step pending" id="step-bls"><span class="step-icon"></span> Retrieving BLS state &amp; industry jobs</div>
    <div class="loading-step pending" id="step-ai"><span class="step-icon"></span> Running AI impact analysis</div>
    <div class="loading-step pending" id="step-dashboard"><span class="step-icon"></span> Generating interactive dashboard</div>
  </div>
  <div class="loading-elapsed" id="loading-elapsed">Elapsed: 0s</div>
</div>

<script>
(function() {
  const overlay = document.getElementById('loading-overlay');
  if (!overlay) return;
  const steps = ['step-onet','step-skills','step-industries','step-bls','step-ai','step-dashboard'];
  const durations = [3, 5, 12, 20, 5, 3]; // approximate seconds per step
  let elapsed = 0, timer, stepTimer;

  function advanceSteps() {
    let cumulative = 0;
    for (let i = 0; i < steps.length; i++) {
      const el = document.getElementById(steps[i]);
      cumulative += durations[i];
      if (elapsed >= cumulative) {
        el.className = 'loading-step done';
      } else if (elapsed >= cumulative - durations[i]) {
        el.className = 'loading-step active';
      } else {
        el.className = 'loading-step pending';
      }
    }
  }

  function startLoading() {
    overlay.classList.add('active');
    elapsed = 0;
    timer = setInterval(function() {
      elapsed++;
      document.getElementById('loading-elapsed').textContent = 'Elapsed: ' + elapsed + 's';
      advanceSteps();
    }, 1000);
  }

  // Intercept clicks on occupation links
  document.querySelectorAll('.occ-link').forEach(function(link) {
    link.addEventListener('click', function(e) {
      startLoading();
    });
  });
})();
</script>
</body>
</html>"""


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Landing page with search, or show search results."""
    keyword = request.args.get("q", "").strip()
    if not keyword:
        return render_template_string(LANDING_HTML, results=None, keyword="", error=None)

    if not API_KEY:
        return render_template_string(
            LANDING_HTML, results=None, keyword=keyword,
            error="Server misconfigured: O*NET API key not set. Contact the administrator."
        )

    try:
        results = search_occupations(keyword, API_KEY)
    except SystemExit:
        return render_template_string(
            LANDING_HTML, results=None, keyword=keyword,
            error="Failed to connect to the O*NET API. Please try again later."
        )
    except Exception as e:
        return render_template_string(
            LANDING_HTML, results=None, keyword=keyword,
            error=f"Search failed: {html_lib.escape(str(e))}"
        )

    if not results:
        return render_template_string(
            LANDING_HTML, results=None, keyword=keyword,
            error=f"No occupations found for \"{html_lib.escape(keyword)}\". Try a different keyword."
        )

    return render_template_string(LANDING_HTML, results=results, keyword=keyword, error=None)


@app.route("/dashboard")
def dashboard():
    """Generate and serve the full interactive dashboard for an occupation."""
    code = request.args.get("code", "").strip()
    if not code:
        return render_template_string(LANDING_HTML, results=None, keyword="",
                                      error="No occupation code provided.")

    if not API_KEY:
        return render_template_string(
            LANDING_HTML, results=None, keyword="",
            error="Server misconfigured: O*NET API key not set."
        )

    try:
        summary = get_occupation_summary(code, API_KEY)
        tasks = get_occupation_tasks(code, API_KEY)
        skills = get_occupation_elements(code, "skills", API_KEY)
        knowledge = get_occupation_elements(code, "knowledge", API_KEY)
        abilities = get_occupation_elements(code, "abilities", API_KEY)
        education = get_education_requirements(code, API_KEY)
        job_zone = get_job_zone(code, API_KEY)
        technologies = get_hot_technologies(code, API_KEY)
        industries = get_occupation_industries(code, API_KEY)
        bls_national = get_bls_national_employment(code, BLS_KEY)
        bls_by_state = get_bls_employment_by_state(code, BLS_KEY)
        bls_by_industry = get_bls_employment_by_industry(code, BLS_KEY)
        ai_impact = analyze_ai_impact(summary, tasks, skills, knowledge, abilities)
        dashboard_html = generate_dashboard(
            summary, tasks, skills, knowledge, abilities, ai_impact,
            industries=industries, education=education,
            job_zone=job_zone, technologies=technologies,
            bls_by_state=bls_by_state, bls_by_industry=bls_by_industry,
            bls_national=bls_national
        )
    except SystemExit:
        return render_template_string(
            LANDING_HTML, results=None, keyword="",
            error=f"Failed to fetch data for occupation {html_lib.escape(code)}. The O*NET API may be unavailable."
        )
    except Exception as e:
        return render_template_string(
            LANDING_HTML, results=None, keyword="",
            error=f"Dashboard generation failed: {html_lib.escape(str(e))}"
        )

    return Response(dashboard_html, mimetype="text/html")


@app.route("/health")
def health():
    """Health check endpoint for Render."""
    return {"status": "ok", "api_key_configured": bool(API_KEY)}


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
