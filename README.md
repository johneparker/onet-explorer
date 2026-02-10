# O*NET Occupation Explorer

An interactive HTML dashboard that pulls occupation data from the [O\*NET Web Services API](https://services.onetcenter.org/) and presents tasks, skills, knowledge, abilities, and **AI Impact analysis** in a self-contained, browser-ready report.

## Features

- **Occupation Search** — search by keyword and select from matching O\*NET occupations
- **Interactive Dashboard** — single-file HTML with tabbed navigation and Chart.js visualizations
- **Six Analysis Tabs:**
  - **Overview** — occupation summary with top skills, knowledge, and abilities at a glance
  - **AI Impact** — AI automation/augmentation scoring, recommended AI agents, and AI-era skills
  - **Tasks** — sortable task list ranked by importance
  - **Skills** — horizontal bar chart of skill importance ratings
  - **Knowledge** — knowledge domain analysis
  - **Abilities** — ability requirements breakdown
- **AI Impact Analysis Engine** — classifies every task as *automate*, *augment*, or *human-essential* using keyword pattern matching, then recommends relevant AI agents and skills
- **Zero Dependencies** — uses only Python standard library (`urllib`, `json`, `base64`, `argparse`, `re`, `html`)

## Prerequisites

1. **Python 3.7+**
2. **O\*NET Web Services account** — register free at [services.onetcenter.org](https://services.onetcenter.org/)

## Quick Start

```bash
# Clone the repo
git clone https://github.com/johneparker/onet-explorer.git
cd onet-explorer

# Run with credentials as arguments
python onet_explorer.py "software developer" --username YOUR_USERNAME --password YOUR_PASSWORD

# Or set environment variables
export ONET_USERNAME=your_username
export ONET_PASSWORD=your_password
python onet_explorer.py "registered nurse"
```

The script will:
1. Search O\*NET for matching occupations
2. Let you select one interactively
3. Pull tasks, skills, knowledge, and abilities data
4. Run AI Impact analysis
5. Generate a self-contained HTML dashboard (e.g., `onet_15-1252.00.html`)

Open the generated HTML file in any browser — no server required.

## Usage

```
python onet_explorer.py [-h] [--username USERNAME] [--password PASSWORD]
                        [--output OUTPUT] keyword
```

| Argument | Description |
|----------|-------------|
| `keyword` | Occupation keyword to search (e.g., `"data scientist"`) |
| `--username` | O\*NET API username (or set `ONET_USERNAME` env var) |
| `--password` | O\*NET API password (or set `ONET_PASSWORD` env var) |
| `--output`, `-o` | Output HTML filename (default: `onet_<code>.html`) |

### Examples

```bash
python onet_explorer.py "financial analyst"
python onet_explorer.py "registered nurse" -o nurse_dashboard.html
python onet_explorer.py "project manager" --username myuser --password mypass
```

## AI Impact Analysis

The AI Impact tab provides a data-driven assessment of how artificial intelligence may affect the occupation:

- **Overall AI Impact Score** (0–100) — weighted by task importance
- **Task Classification** — each task categorized as automate, augment, or human-essential
- **AI Agent Recommendations** — up to 8 relevant AI agents (e.g., Data Analytics Agent, Code Assistant, Document Processing Agent) with relevance scores
- **AI Skills Recommendations** — prioritized list of AI-era skills professionals should develop
- **Strategic Outlook** — narrative summary of the AI impact profile

The classification engine uses regex-based keyword pattern matching against three category dictionaries, with weighted scoring that includes a conservative bias toward human-essential classification to avoid over-claiming AI capability.

## Web App (Render Deployment)

A Flask web interface is also included, letting users search and view dashboards in the browser without running the CLI.

### Deploy to Render

1. Fork or push this repo to your GitHub account
2. Go to [render.com](https://render.com) → **New** → **Blueprint**
3. Connect this repository — Render will auto-detect `render.yaml`
4. Set the environment variables when prompted:
   - `ONET_USERNAME` — your O\*NET API username
   - `ONET_PASSWORD` — your O\*NET API password
5. Deploy — your app will be live at `https://onet-explorer.onrender.com`

### Run Locally

```bash
pip install -r requirements.txt
export ONET_USERNAME=your_username
export ONET_PASSWORD=your_password
python app.py
```

Then visit `http://localhost:5000` in your browser.

## How It Works

1. **API Client** — makes authenticated requests to O\*NET Web Services REST API
2. **Data Retrieval** — fetches occupation summary, tasks, skills, knowledge, and abilities
3. **AI Analysis** — classifies tasks, scores overall impact, recommends agents and skills
4. **Dashboard Generation** — produces a single HTML file with embedded CSS, JavaScript, Chart.js charts, and JSON data

## License

MIT
