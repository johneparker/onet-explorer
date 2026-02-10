#!/usr/bin/env python3
"""
O*NET Occupation Explorer
=========================
Queries the O*NET Web Services API for a given occupation and generates
an interactive HTML dashboard showing tasks, skills, knowledge, abilities,
and an AI Impact analysis with automation potential and agent recommendations.

Usage:
    python onet_explorer.py "software developer"
    python onet_explorer.py "registered nurse" --api-key YOUR_API_KEY

API key can also be set via environment variable:
    export ONET_API_KEY=your_api_key

Register and generate an API key at: https://services.onetcenter.org/
"""

import argparse
import html
import json
import os
import re
import sys
import textwrap
from datetime import datetime
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


# ─── API Client ───────────────────────────────────────────────────────────────

BASE_URL = "https://api-v2.onetcenter.org/"


def make_request(endpoint: str, api_key: str, params: dict = None) -> dict:
    """Make an authenticated request to the O*NET API and return JSON."""
    # Strip leading slash — v2 base URL already has trailing slash
    endpoint = endpoint.lstrip("/")
    url = f"{BASE_URL}{endpoint}"
    if params:
        query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
        url = f"{url}?{query}"

    req = Request(url)
    req.add_header("X-API-Key", api_key)
    req.add_header("Accept", "application/json")

    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        if e.code == 401:
            raise RuntimeError("Authentication failed. Check your O*NET API key.")
        elif e.code == 422:
            raise RuntimeError(f"Invalid request — {e.read().decode()}")
        else:
            raise RuntimeError(f"HTTP {e.code} — {e.reason}")


def _fetch_all_pages(endpoint: str, api_key: str, list_key: str) -> list:
    """Fetch all pages of a paginated O*NET v2 endpoint."""
    from urllib.parse import urlparse
    all_items = []
    data = make_request(endpoint, api_key)
    all_items.extend(data.get(list_key, []))

    # Follow pagination links until exhausted
    while data.get("next"):
        next_url = data["next"]
        parsed = urlparse(next_url)
        path = parsed.path.lstrip("/")
        qs = parsed.query
        full_endpoint = f"{path}?{qs}" if qs else path
        data = make_request(full_endpoint, api_key)
        all_items.extend(data.get(list_key, []))

    return all_items


def search_occupations(keyword: str, api_key: str) -> list:
    """Search for occupations by keyword. Returns list of {code, title}."""
    data = make_request("online/search", api_key, {"keyword": keyword})
    occupations = data.get("occupation", [])
    return [{"code": occ["code"], "title": occ["title"]} for occ in occupations]


def get_occupation_tasks(code: str, api_key: str) -> list:
    """Fetch all tasks for an occupation (follows pagination)."""
    raw_tasks = _fetch_all_pages(
        f"online/occupations/{quote(code, safe='')}/details/tasks",
        api_key, "task"
    )
    tasks = []
    for t in raw_tasks:
        # v2 uses 'title' instead of 'statement', flat 'importance' instead of nested score
        tasks.append({
            "statement": t.get("title", t.get("statement", "")),
            "category": t.get("category", ""),
            "score": t.get("importance", 0),
            "important": t.get("importance", 0) >= 50,
        })
    return sorted(tasks, key=lambda x: x["score"], reverse=True)


def get_occupation_elements(code: str, element_type: str, api_key: str) -> list:
    """Fetch all skills, knowledge, or abilities (follows pagination)."""
    raw_elements = _fetch_all_pages(
        f"online/occupations/{quote(code, safe='')}/details/{element_type}",
        api_key, "element"
    )
    elements = []
    for el in raw_elements:
        # v2 uses flat 'importance' integer (0-100)
        importance = el.get("importance", 0)
        elements.append({
            "name": el.get("name", ""),
            "description": el.get("description", ""),
            "score": importance,
            "important": importance >= 50,
        })
    return sorted(elements, key=lambda x: x["score"], reverse=True)


def get_occupation_summary(code: str, api_key: str) -> dict:
    """Fetch the occupation summary/description."""
    data = make_request(
        f"online/occupations/{quote(code, safe='')}",
        api_key
    )
    return {
        "title": data.get("title", ""),
        "description": data.get("description", ""),
        "code": data.get("code", code),
    }


# ─── AI Impact Analysis Engine ───────────────────────────────────────────────

# Keywords signaling high AI automation potential (routine, data-driven, repetitive)
_AUTOMATE_KEYWORDS = [
    r"\bschedul\w*", r"\btrack\w*", r"\bmonitor\w*", r"\blog\w*\b", r"\brecord\w*",
    r"\bcompil\w*", r"\bfile\w*", r"\bformat\w*", r"\bsort\w*", r"\bdata.?entry",
    r"\btranscri\w*", r"\bcalculat\w*", r"\btabulat\w*", r"\binventory",
    r"\binvoic\w*", r"\bbookkeep\w*", r"\bpayroll", r"\bprocess\w* (claim|order|form|request)",
    r"\brout\w*", r"\bgenerat\w* report", r"\bupdat\w* (record|database|system|file|log)",
    r"\bverif\w* (data|record|document|information)", r"\barchiv\w*",
    r"\bcatalog\w*", r"\bindex\w*", r"\bclassif\w* (document|record|data)",
]

# Keywords signaling high AI augmentation potential (complex analysis, creative support)
_AUGMENT_KEYWORDS = [
    r"\banalyz\w*", r"\bresearch\w*", r"\bdesign\w*", r"\bdevelop\w*",
    r"\bwrit\w*", r"\bdraft\w*", r"\breview\w*", r"\bevaluat\w*",
    r"\bdiagnos\w*", r"\bforecast\w*", r"\bplan\w*", r"\boptimiz\w*",
    r"\bmodel\w*", r"\btest\w*", r"\bassess\w*", r"\bexamin\w*",
    r"\bidentif\w* (trend|pattern|issue|problem|risk|opportunity)",
    r"\binterpret\w*", r"\bsynthe\w*", r"\bsummariz\w*",
    r"\binvestigat\w*", r"\bprogram\w*", r"\bcode\w*", r"\baudit\w*",
    r"\bcreat\w* (content|design|model|plan|strateg)",
    r"\bpredicti\w*", r"\bstatistic\w*", r"\bsimulat\w*",
]

# Keywords signaling tasks that remain human-essential (relational, ethical, physical)
_HUMAN_KEYWORDS = [
    r"\bnegotiat\w*", r"\blead\w*", r"\bmentor\w*", r"\bcounsel\w*",
    r"\bpersuad\w*", r"\bmotivat\w*", r"\bmediat\w*", r"\bempathi\w*",
    r"\bsupervis\w*", r"\bmanag\w* (team|staff|people|employee|personnel)",
    r"\bcoach\w*", r"\btrain\w* (staff|employee|personnel|team)",
    r"\bresolv\w* (conflict|dispute)", r"\bbuild\w* (relationship|rapport|trust)",
    r"\bphysical\w*", r"\bhand\w*", r"\boperat\w* (machine|equipment|vehicle)",
    r"\bpresent\w* (to|before|at)", r"\bdeliver\w* (speech|presentation|lecture)",
    r"\bconvinc\w*", r"\binspir\w*", r"\bethic\w*",
    r"\bemergenc\w*", r"\bcrisis\w*", r"\bpatient\w* care",
    r"\bsafety\w*", r"\bprotect\w*",
]

# AI agent catalog: (name, icon, description, trigger keywords)
_AI_AGENT_CATALOG = [
    {
        "name": "Data Analytics Agent",
        "icon": "chart-bar",
        "desc": "Automates data collection, statistical analysis, trend identification, and dashboard generation from structured and unstructured data sources.",
        "business_value": "Reduces analysis cycle time by 60-80%, enabling faster decision-making and freeing analysts for strategic interpretation.",
        "triggers": ["analyz", "data", "statistic", "report", "trend", "forecast", "metric", "dashboard"],
    },
    {
        "name": "Document Processing Agent",
        "icon": "file-text",
        "desc": "Extracts, classifies, summarizes, and routes documents. Handles forms, contracts, invoices, and compliance paperwork with high accuracy.",
        "business_value": "Eliminates 70-90% of manual document handling, cutting processing costs and reducing error rates below 2%.",
        "triggers": ["document", "record", "file", "form", "report", "compil", "review document", "paperwork", "contract", "invoice"],
    },
    {
        "name": "Research & Intelligence Agent",
        "icon": "search",
        "desc": "Conducts multi-source research, synthesizes findings, monitors competitive landscapes, and generates briefing documents with citations.",
        "business_value": "Compresses weeks of research into hours, surfacing relevant insights from thousands of sources simultaneously.",
        "triggers": ["research", "investigat", "literature", "review", "survey", "study", "evaluat", "assess", "information gathering"],
    },
    {
        "name": "Content Generation Agent",
        "icon": "pen-tool",
        "desc": "Drafts communications, technical writing, marketing copy, reports, and presentations aligned to brand voice and audience requirements.",
        "business_value": "Produces first drafts 10x faster, allowing professionals to focus on refinement, strategy, and stakeholder alignment.",
        "triggers": ["writ", "draft", "communicat", "corresponden", "present", "content", "report", "memo", "proposal"],
    },
    {
        "name": "Code & Technical Assistant Agent",
        "icon": "terminal",
        "desc": "Generates, reviews, debugs, and documents code. Assists with architecture decisions, testing strategies, and technical documentation.",
        "business_value": "Accelerates development velocity by 30-50%, reduces bug density, and automates routine code maintenance tasks.",
        "triggers": ["code", "program", "software", "develop", "debug", "test", "system", "technical", "engineer", "algorithm"],
    },
    {
        "name": "Scheduling & Workflow Agent",
        "icon": "calendar",
        "desc": "Manages calendars, coordinates meetings, automates approval workflows, tracks deadlines, and optimizes resource allocation across teams.",
        "business_value": "Recovers 5-10 hours per week per professional in coordination overhead, eliminating scheduling conflicts.",
        "triggers": ["schedul", "coordinat", "calendar", "meeting", "workflow", "deadline", "assign", "prioritiz", "allocat"],
    },
    {
        "name": "Customer Interaction Agent",
        "icon": "message-circle",
        "desc": "Handles customer inquiries, triages support requests, provides personalized responses, and escalates complex issues to human specialists.",
        "business_value": "Resolves 40-60% of routine inquiries autonomously, improving response times from hours to seconds.",
        "triggers": ["customer", "client", "patient", "consult", "service", "support", "inquir", "respond", "assist"],
    },
    {
        "name": "Financial Analysis Agent",
        "icon": "dollar-sign",
        "desc": "Performs budget analysis, financial modeling, variance reporting, invoice processing, and regulatory compliance checking for financial operations.",
        "business_value": "Automates 50-70% of routine financial tasks while improving accuracy and enabling real-time financial visibility.",
        "triggers": ["financ", "budget", "account", "audit", "tax", "cost", "revenue", "invoic", "payroll", "compliance"],
    },
    {
        "name": "Quality & Compliance Agent",
        "icon": "shield",
        "desc": "Monitors standards adherence, performs automated inspections, tracks regulatory changes, and generates compliance documentation.",
        "business_value": "Reduces compliance gaps by continuous monitoring, cutting audit preparation time by 60% and violation risk by 40%.",
        "triggers": ["quality", "compliance", "regulat", "standard", "inspect", "audit", "safety", "certif", "policy"],
    },
    {
        "name": "Training & Knowledge Agent",
        "icon": "book-open",
        "desc": "Creates personalized learning paths, generates training materials, answers knowledge-base queries, and tracks skill development progress.",
        "business_value": "Reduces onboarding time by 40%, provides 24/7 knowledge access, and adapts training to individual learning pace.",
        "triggers": ["train", "educat", "instruct", "teach", "learn", "develop skill", "mentor", "onboard", "knowledge"],
    },
]

# AI-era skills to recommend based on role characteristics
_AI_SKILLS_CATALOG = [
    {
        "name": "Prompt Engineering & AI Direction",
        "desc": "Crafting effective instructions for AI systems to produce accurate, relevant outputs. Includes iterative refinement, context-setting, and output validation techniques.",
        "relevance": "universal",
        "triggers": [],
    },
    {
        "name": "AI Output Validation & Critical Review",
        "desc": "Evaluating AI-generated content for accuracy, bias, hallucination, and alignment with professional standards before use in decision-making.",
        "relevance": "universal",
        "triggers": [],
    },
    {
        "name": "Human-AI Workflow Design",
        "desc": "Designing processes that optimally distribute tasks between human professionals and AI agents, maximizing both efficiency and quality.",
        "relevance": "universal",
        "triggers": [],
    },
    {
        "name": "Data Literacy for AI",
        "desc": "Understanding data quality, statistical concepts, and dataset characteristics to effectively leverage AI analytics and interpret machine-generated insights.",
        "relevance": "data",
        "triggers": ["analyz", "data", "statistic", "research", "evaluat", "assess", "report", "metric"],
    },
    {
        "name": "AI-Augmented Decision Making",
        "desc": "Integrating AI-generated analysis and recommendations into professional judgment frameworks while maintaining accountability and ethical standards.",
        "relevance": "analysis",
        "triggers": ["evaluat", "assess", "diagnos", "plan", "strateg", "decision", "recommend", "priorit"],
    },
    {
        "name": "Automation & Agent Orchestration",
        "desc": "Selecting, configuring, and chaining AI agents to automate multi-step business processes. Includes monitoring agent performance and handling exceptions.",
        "relevance": "process",
        "triggers": ["process", "coordinat", "manag", "workflow", "schedul", "system", "implement"],
    },
    {
        "name": "AI Ethics & Responsible Use",
        "desc": "Recognizing bias risks, privacy implications, and ethical boundaries when deploying AI in professional contexts. Ensuring equitable and transparent AI use.",
        "relevance": "ethics",
        "triggers": ["ethic", "regulat", "compliance", "policy", "patient", "client", "counsel", "legal"],
    },
    {
        "name": "Creative AI Collaboration",
        "desc": "Using generative AI as a creative partner for ideation, prototyping, and content development while preserving originality and professional voice.",
        "relevance": "creative",
        "triggers": ["design", "creat", "develop", "writ", "innovat", "concept", "prototype", "content"],
    },
    {
        "name": "AI-Powered Communication",
        "desc": "Leveraging AI tools for drafting, translating, summarizing, and personalizing communications across channels and audiences at scale.",
        "relevance": "communication",
        "triggers": ["communicat", "present", "writ", "correspond", "report", "client", "stakeholder"],
    },
    {
        "name": "Continuous Learning & AI Adaptation",
        "desc": "Staying current with rapidly evolving AI capabilities, evaluating new tools, and continuously updating professional workflows to leverage emerging technology.",
        "relevance": "universal",
        "triggers": [],
    },
]


def _match_keywords(text: str, patterns: list) -> int:
    """Count how many keyword patterns match in the text."""
    text_lower = text.lower()
    count = 0
    for pattern in patterns:
        if re.search(pattern, text_lower):
            count += 1
    return count


def classify_task_ai_impact(statement: str) -> dict:
    """Classify a single task's AI impact potential.

    Returns {classification, confidence, rationale} where classification is one of:
        'automate' — AI can fully handle this task
        'augment'  — AI significantly enhances human performance
        'human'    — Task remains primarily human-driven
    """
    auto_hits = _match_keywords(statement, _AUTOMATE_KEYWORDS)
    augment_hits = _match_keywords(statement, _AUGMENT_KEYWORDS)
    human_hits = _match_keywords(statement, _HUMAN_KEYWORDS)

    total = auto_hits + augment_hits + human_hits
    if total == 0:
        # Default to augment for unclassified tasks
        return {"classification": "augment", "confidence": 40,
                "rationale": "General professional task with moderate AI augmentation potential."}

    # Weighted scoring
    auto_score = auto_hits * 1.0
    augment_score = augment_hits * 0.85
    human_score = human_hits * 1.1  # slight bias toward human to avoid over-claiming

    max_score = max(auto_score, augment_score, human_score)
    confidence = min(95, int(40 + (max_score / total) * 55))

    if max_score == auto_score and auto_score > augment_score:
        return {"classification": "automate", "confidence": confidence,
                "rationale": "Involves routine, data-driven, or repetitive processes well-suited to AI automation."}
    elif max_score == human_score and human_score > augment_score:
        return {"classification": "human", "confidence": confidence,
                "rationale": "Requires interpersonal judgment, physical presence, ethical reasoning, or leadership that remains human-essential."}
    else:
        return {"classification": "augment", "confidence": confidence,
                "rationale": "Complex analytical or creative work where AI serves as a powerful co-pilot enhancing speed and quality."}


def recommend_agents(tasks: list, skills: list, knowledge: list) -> list:
    """Score and rank AI agents based on relevance to this occupation."""
    all_text = " ".join(
        [t["statement"] for t in tasks] +
        [s["name"] + " " + s.get("description", "") for s in skills] +
        [k["name"] + " " + k.get("description", "") for k in knowledge]
    ).lower()

    scored_agents = []
    for agent in _AI_AGENT_CATALOG:
        score = sum(1 for kw in agent["triggers"] if kw in all_text)
        if score > 0:
            scored_agents.append({**agent, "relevance_score": min(100, score * 15)})

    scored_agents.sort(key=lambda a: a["relevance_score"], reverse=True)
    return scored_agents[:8]  # top 8 most relevant


def recommend_ai_skills(tasks: list, task_classifications: list) -> list:
    """Recommend AI-era skills based on occupation characteristics."""
    all_text = " ".join(t["statement"] for t in tasks).lower()
    auto_pct = sum(1 for c in task_classifications if c["classification"] == "automate") / max(len(task_classifications), 1)

    recommended = []
    for skill in _AI_SKILLS_CATALOG:
        # Universal skills always included
        if skill["relevance"] == "universal":
            recommended.append({**skill, "priority": "Essential"})
            continue

        # Check trigger keyword matches
        matches = sum(1 for kw in skill["triggers"] if kw in all_text)
        if matches >= 2:
            recommended.append({**skill, "priority": "High"})
        elif matches >= 1:
            recommended.append({**skill, "priority": "Recommended"})

    # If many tasks are automatable, boost orchestration skills
    if auto_pct > 0.3:
        for s in recommended:
            if "Orchestration" in s["name"]:
                s["priority"] = "Essential"

    return recommended


def analyze_ai_impact(summary: dict, tasks: list, skills: list,
                      knowledge: list, abilities: list) -> dict:
    """Produce a complete AI impact analysis for an occupation.

    Returns a dict with:
        role_summary   — narrative description of AI's impact on the role
        task_analysis  — per-task classification list
        distribution   — {automate: n, augment: n, human: n}
        overall_score  — 0-100 composite AI impact score
        agents         — ranked list of recommended AI agents
        ai_skills      — recommended skills for AI-era readiness
        outlook        — strategic outlook narrative
    """
    # Classify every task
    task_analysis = []
    for t in tasks:
        classification = classify_task_ai_impact(t["statement"])
        task_analysis.append({
            "statement": t["statement"],
            "importance": t["score"]["value"] if isinstance(t["score"], dict) else t["score"],
            "category": t.get("category", ""),
            **classification,
        })

    # Distribution counts
    n_auto = sum(1 for t in task_analysis if t["classification"] == "automate")
    n_augment = sum(1 for t in task_analysis if t["classification"] == "augment")
    n_human = sum(1 for t in task_analysis if t["classification"] == "human")
    n_total = max(len(task_analysis), 1)

    distribution = {"automate": n_auto, "augment": n_augment, "human": n_human}

    # Weighted importance score: tasks with higher O*NET importance carry more weight
    if task_analysis:
        weighted_auto = sum(t["importance"] for t in task_analysis if t["classification"] == "automate")
        weighted_augment = sum(t["importance"] for t in task_analysis if t["classification"] == "augment")
        weighted_total = sum(t["importance"] for t in task_analysis)
        overall_score = int(((weighted_auto * 1.0 + weighted_augment * 0.6) / max(weighted_total, 1)) * 100)
        overall_score = min(95, max(10, overall_score))
    else:
        overall_score = 50

    # Determine impact level label
    if overall_score >= 75:
        impact_level = "Transformative"
        impact_color = "#EF4444"
    elif overall_score >= 55:
        impact_level = "Significant"
        impact_color = "#F59E0B"
    elif overall_score >= 35:
        impact_level = "Moderate"
        impact_color = "#3B82F6"
    else:
        impact_level = "Limited"
        impact_color = "#10B981"

    # Generate narrative
    title = summary.get("title", "this occupation")
    auto_pct = int((n_auto / n_total) * 100)
    augment_pct = int((n_augment / n_total) * 100)
    human_pct = int((n_human / n_total) * 100)

    role_summary = (
        f"AI is projected to have a <strong>{impact_level.lower()}</strong> impact on "
        f"<strong>{html.escape(title)}</strong>. Analysis of {n_total} core tasks reveals that "
        f"approximately {auto_pct}% of tasks have high automation potential, "
        f"{augment_pct}% can be significantly augmented by AI co-pilots, and "
        f"{human_pct}% remain primarily human-driven. "
    )

    if overall_score >= 65:
        role_summary += (
            "Professionals in this role should proactively develop AI collaboration skills "
            "and prepare for substantial workflow transformation. Organizations should begin "
            "piloting AI agents for high-automation tasks while upskilling staff on AI-augmented "
            "processes."
        )
    elif overall_score >= 40:
        role_summary += (
            "This role will evolve significantly as AI tools mature. The focus should be on "
            "adopting AI co-pilots for analytical and research tasks while preserving the "
            "human expertise that defines professional value in this occupation."
        )
    else:
        role_summary += (
            "While AI will provide useful support tools, the core human skills of this role — "
            "interpersonal judgment, ethical reasoning, and physical presence — keep it highly "
            "resistant to displacement. The emphasis should be on AI as an efficiency multiplier."
        )

    # Strategic outlook
    if overall_score >= 65:
        outlook = (
            "High-impact role transformation expected within 2-4 years. Organizations should "
            "establish AI centers of excellence and begin phased automation of routine tasks. "
            "Professionals should invest heavily in AI orchestration and validation skills to "
            "remain competitive. The role itself will likely evolve into a more strategic, "
            "supervisory position overseeing AI-augmented workflows."
        )
    elif overall_score >= 40:
        outlook = (
            "Steady evolution over 3-5 years as AI augmentation tools become mainstream. "
            "Early adopters will gain significant productivity advantages. The core role persists "
            "but with higher expectations for output volume and analytical depth. Investment in "
            "AI literacy and tool proficiency will increasingly differentiate top performers."
        )
    else:
        outlook = (
            "Gradual adoption of AI support tools over 3-7 years. The fundamentally human "
            "nature of this role provides strong resilience against displacement. AI will primarily "
            "serve as an efficiency aid for administrative and analytical sub-tasks, allowing "
            "professionals to dedicate more time to their highest-value activities."
        )

    agents = recommend_agents(tasks, skills, knowledge)
    ai_skills = recommend_ai_skills(tasks, task_analysis)

    return {
        "role_summary": role_summary,
        "task_analysis": task_analysis,
        "distribution": distribution,
        "overall_score": overall_score,
        "impact_level": impact_level,
        "impact_color": impact_color,
        "agents": agents,
        "ai_skills": ai_skills,
        "outlook": outlook,
    }


# ─── Dashboard Generator ─────────────────────────────────────────────────────

def generate_dashboard(summary: dict, tasks: list, skills: list,
                       knowledge: list, abilities: list, ai_impact: dict) -> str:
    """Generate a self-contained interactive HTML dashboard."""

    title = html.escape(summary["title"])
    code = html.escape(summary["code"])
    description = html.escape(summary["description"])
    generated = datetime.now().strftime("%B %d, %Y at %I:%M %p")

    # Prepare JSON data for embedding
    tasks_json = json.dumps(tasks)
    skills_json = json.dumps(skills)
    knowledge_json = json.dumps(knowledge)
    abilities_json = json.dumps(abilities)
    ai_impact_json = json.dumps(ai_impact)

    return textwrap.dedent(f"""\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>O*NET Explorer — {title}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.1" integrity="sha384-jb8JQMbMoBUzgWatfe6COACi2ljcDdZQ2OxczGA3bGNeWe+6DChMTBJemed7ZnvJ" crossorigin="anonymous"></script>
    <style>
        :root {{
            --bg-primary: #f5f6fa;
            --bg-card: #ffffff;
            --bg-header: #1B2A4A;
            --text-primary: #1a1a2e;
            --text-secondary: #6b7280;
            --text-on-dark: #ffffff;
            --accent: #3B82F6;
            --accent-light: #EFF6FF;
            --skill-color: #3B82F6;
            --knowledge-color: #10B981;
            --ability-color: #8B5CF6;
            --task-color: #F59E0B;
            --ai-color: #EC4899;
            --gap: 16px;
            --radius: 10px;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.6;
        }}
        .container {{ max-width: 1400px; margin: 0 auto; padding: var(--gap); }}

        /* Header */
        .header {{
            background: var(--bg-header);
            color: var(--text-on-dark);
            padding: 28px 32px;
            border-radius: var(--radius);
            margin-bottom: var(--gap);
        }}
        .header h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 4px; }}
        .header .code {{ font-size: 13px; opacity: 0.7; font-family: monospace; margin-bottom: 12px; }}
        .header .desc {{ font-size: 14px; line-height: 1.7; opacity: 0.9; max-width: 900px; }}
        .back-btn {{
            display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px;
            background: rgba(255,255,255,0.15); color: var(--text-on-dark);
            border: 1px solid rgba(255,255,255,0.25); border-radius: 8px;
            text-decoration: none; font-size: 13px; font-weight: 500;
            margin-bottom: 14px; transition: background 0.2s;
        }}
        .back-btn:hover {{ background: rgba(255,255,255,0.25); }}
        @media print {{ .back-btn {{ display: none; }} }}

        /* KPI row */
        .kpi-row {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
            gap: var(--gap);
            margin-bottom: var(--gap);
        }}
        .kpi-card {{
            background: var(--bg-card);
            border-radius: var(--radius);
            padding: 20px 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
            border-left: 4px solid var(--accent);
            transition: transform 0.15s;
        }}
        .kpi-card:hover {{ transform: translateY(-2px); box-shadow: 0 4px 12px rgba(0,0,0,0.1); }}
        .kpi-card.skills {{ border-left-color: var(--skill-color); }}
        .kpi-card.knowledge {{ border-left-color: var(--knowledge-color); }}
        .kpi-card.abilities {{ border-left-color: var(--ability-color); }}
        .kpi-card.tasks {{ border-left-color: var(--task-color); }}
        .kpi-card.ai-impact {{ border-left-color: var(--ai-color); }}
        .kpi-label {{ font-size: 12px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 4px; }}
        .kpi-value {{ font-size: 32px; font-weight: 700; }}
        .kpi-sub {{ font-size: 11px; color: var(--text-secondary); margin-top: 2px; }}

        /* Tabs */
        .tab-bar {{
            display: flex;
            gap: 4px;
            margin-bottom: var(--gap);
            border-bottom: 2px solid #e5e7eb;
            padding-bottom: 0;
            overflow-x: auto;
        }}
        .tab {{
            padding: 10px 20px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            border: none;
            background: none;
            color: var(--text-secondary);
            border-bottom: 3px solid transparent;
            margin-bottom: -2px;
            transition: all 0.2s;
            white-space: nowrap;
        }}
        .tab:hover {{ color: var(--text-primary); }}
        .tab.active {{ color: var(--accent); border-bottom-color: var(--accent); }}
        .tab.ai-tab.active {{ color: var(--ai-color); border-bottom-color: var(--ai-color); }}
        .tab-content {{ display: none; }}
        .tab-content.active {{ display: block; }}

        /* Charts */
        .chart-row {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
            gap: var(--gap);
            margin-bottom: var(--gap);
        }}
        .chart-card {{
            background: var(--bg-card);
            border-radius: var(--radius);
            padding: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        }}
        .chart-card h3 {{
            font-size: 15px;
            font-weight: 600;
            margin-bottom: 16px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .chart-card h3 .dot {{
            width: 10px; height: 10px;
            border-radius: 50%;
            display: inline-block;
        }}
        .chart-card canvas {{ max-height: 400px; }}

        /* Tables */
        .table-card {{
            background: var(--bg-card);
            border-radius: var(--radius);
            padding: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
            margin-bottom: var(--gap);
        }}
        .table-card h3 {{ font-size: 15px; font-weight: 600; margin-bottom: 16px; }}
        .search-box {{
            width: 100%;
            padding: 10px 14px;
            border: 1px solid #e5e7eb;
            border-radius: 6px;
            font-size: 14px;
            margin-bottom: 12px;
        }}
        .search-box:focus {{ outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(59,130,246,0.1); }}
        table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        thead th {{
            text-align: left;
            padding: 10px 12px;
            border-bottom: 2px solid #e5e7eb;
            color: var(--text-secondary);
            font-weight: 600;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            cursor: pointer;
            user-select: none;
            white-space: nowrap;
        }}
        thead th:hover {{ color: var(--text-primary); background: #f9fafb; }}
        tbody td {{ padding: 10px 12px; border-bottom: 1px solid #f3f4f6; }}
        tbody tr:hover {{ background: #f9fafb; }}
        .badge {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
        }}
        .badge-core {{ background: #DBEAFE; color: #1E40AF; }}
        .badge-supplemental {{ background: #FEF3C7; color: #92400E; }}
        .badge-important {{ background: #D1FAE5; color: #065F46; }}
        .badge-automate {{ background: #FEE2E2; color: #991B1B; }}
        .badge-augment {{ background: #FEF3C7; color: #92400E; }}
        .badge-human {{ background: #D1FAE5; color: #065F46; }}
        .badge-essential {{ background: #FCE7F3; color: #9D174D; }}
        .badge-high {{ background: #DBEAFE; color: #1E40AF; }}
        .badge-recommended {{ background: #F3F4F6; color: #4B5563; }}
        .score-bar {{
            height: 6px;
            background: #e5e7eb;
            border-radius: 3px;
            overflow: hidden;
            min-width: 80px;
        }}
        .score-fill {{
            height: 100%;
            border-radius: 3px;
            transition: width 0.3s;
        }}

        /* Detail cards */
        .detail-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
            gap: 12px;
        }}
        .detail-item {{
            background: #f9fafb;
            border-radius: 8px;
            padding: 14px 16px;
            border: 1px solid #f3f4f6;
            transition: all 0.15s;
        }}
        .detail-item:hover {{ background: var(--accent-light); border-color: #BFDBFE; }}
        .detail-item .name {{ font-weight: 600; font-size: 14px; margin-bottom: 4px; }}
        .detail-item .desc {{ font-size: 12px; color: var(--text-secondary); margin-bottom: 8px; }}
        .detail-item .score-row {{ display: flex; align-items: center; gap: 8px; font-size: 12px; }}

        /* AI Impact specific */
        .ai-summary-card {{
            background: linear-gradient(135deg, #1B2A4A 0%, #2D1B4E 100%);
            color: white;
            border-radius: var(--radius);
            padding: 28px 32px;
            margin-bottom: var(--gap);
        }}
        .ai-summary-card h2 {{ font-size: 18px; font-weight: 700; margin-bottom: 12px; display: flex; align-items: center; gap: 10px; }}
        .ai-summary-card .summary-text {{ font-size: 14px; line-height: 1.8; opacity: 0.92; }}
        .ai-summary-card .summary-text strong {{ color: #F9A8D4; }}

        .ai-score-ring {{
            display: flex;
            align-items: center;
            gap: 24px;
            margin-top: 20px;
            flex-wrap: wrap;
        }}
        .ring-container {{ position: relative; width: 100px; height: 100px; }}
        .ring-label {{ text-align: center; margin-top: 6px; font-size: 12px; opacity: 0.8; }}
        .ring-value {{
            position: absolute;
            top: 50%; left: 50%;
            transform: translate(-50%, -50%);
            font-size: 22px;
            font-weight: 800;
        }}
        .ai-metrics {{
            display: flex;
            gap: 24px;
            flex-wrap: wrap;
        }}
        .ai-metric {{
            text-align: center;
        }}
        .ai-metric .val {{ font-size: 26px; font-weight: 800; }}
        .ai-metric .lbl {{ font-size: 11px; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.5px; }}

        .agent-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 14px;
        }}
        .agent-card {{
            background: var(--bg-card);
            border-radius: var(--radius);
            padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
            border-top: 3px solid var(--ai-color);
            transition: all 0.15s;
        }}
        .agent-card:hover {{ transform: translateY(-2px); box-shadow: 0 6px 16px rgba(0,0,0,0.1); }}
        .agent-card .agent-name {{ font-size: 15px; font-weight: 700; margin-bottom: 6px; color: var(--text-primary); }}
        .agent-card .agent-desc {{ font-size: 13px; color: var(--text-secondary); line-height: 1.6; margin-bottom: 10px; }}
        .agent-card .agent-value {{ font-size: 12px; color: #059669; line-height: 1.5; padding: 8px 12px; background: #ECFDF5; border-radius: 6px; }}
        .agent-card .agent-value strong {{ color: #047857; }}
        .agent-card .relevance-bar {{ margin-top: 10px; display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--text-secondary); }}

        .ai-skill-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
            gap: 12px;
        }}
        .ai-skill-card {{
            background: #f9fafb;
            border-radius: 8px;
            padding: 16px 18px;
            border: 1px solid #f3f4f6;
            transition: all 0.15s;
        }}
        .ai-skill-card:hover {{ background: #FDF2F8; border-color: #FBCFE8; }}
        .ai-skill-card .skill-name {{ font-weight: 600; font-size: 14px; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; }}
        .ai-skill-card .skill-desc {{ font-size: 12px; color: var(--text-secondary); line-height: 1.6; }}

        .outlook-card {{
            background: var(--bg-card);
            border-radius: var(--radius);
            padding: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
            margin-bottom: var(--gap);
            border-left: 4px solid var(--ai-color);
        }}
        .outlook-card h3 {{ font-size: 15px; font-weight: 600; margin-bottom: 10px; }}
        .outlook-card p {{ font-size: 14px; color: var(--text-secondary); line-height: 1.8; }}

        .section-label {{
            font-size: 13px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--ai-color);
            margin-bottom: 14px;
            padding-bottom: 8px;
            border-bottom: 2px solid #FCE7F3;
        }}

        /* Footer */
        .footer {{
            text-align: center;
            padding: 20px;
            font-size: 12px;
            color: var(--text-secondary);
        }}

        /* Responsive */
        @media (max-width: 768px) {{
            .chart-row {{ grid-template-columns: 1fr; }}
            .detail-grid, .agent-grid, .ai-skill-grid {{ grid-template-columns: 1fr; }}
            .kpi-row {{ grid-template-columns: repeat(2, 1fr); }}
            .tab {{ padding: 8px 12px; font-size: 13px; }}
            .ai-score-ring {{ flex-direction: column; align-items: flex-start; }}
        }}
        @media print {{
            body {{ background: white; }}
            .container {{ max-width: none; }}
            .kpi-card, .chart-card, .table-card, .agent-card {{ box-shadow: none; border: 1px solid #e5e7eb; }}
            .tab-content {{ display: block !important; page-break-inside: avoid; }}
            .tab-bar {{ display: none; }}
            .ai-summary-card {{ color-adjust: exact; -webkit-print-color-adjust: exact; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <div class="header">
            <a href="/" class="back-btn" id="back-btn">&larr; New Search</a>
            <h1>{title}</h1>
            <div class="code">O*NET-SOC: {code}</div>
            <div class="desc">{description}</div>
        </div>

        <!-- KPI Row -->
        <div class="kpi-row">
            <div class="kpi-card tasks">
                <div class="kpi-label">Tasks</div>
                <div class="kpi-value" id="kpi-tasks">0</div>
            </div>
            <div class="kpi-card skills">
                <div class="kpi-label">Skills</div>
                <div class="kpi-value" id="kpi-skills">0</div>
            </div>
            <div class="kpi-card knowledge">
                <div class="kpi-label">Knowledge Areas</div>
                <div class="kpi-value" id="kpi-knowledge">0</div>
            </div>
            <div class="kpi-card abilities">
                <div class="kpi-label">Abilities</div>
                <div class="kpi-value" id="kpi-abilities">0</div>
            </div>
            <div class="kpi-card ai-impact">
                <div class="kpi-label">AI Impact Score</div>
                <div class="kpi-value" id="kpi-ai-score" style="color:var(--ai-color)">0</div>
                <div class="kpi-sub" id="kpi-ai-level"></div>
            </div>
        </div>

        <!-- Tab Navigation -->
        <div class="tab-bar">
            <button class="tab active" onclick="switchTab('overview', this)">Overview</button>
            <button class="tab" onclick="switchTab('ai-impact', this)">AI Impact</button>
            <button class="tab" onclick="switchTab('tasks', this)">Tasks</button>
            <button class="tab" onclick="switchTab('skills', this)">Skills</button>
            <button class="tab" onclick="switchTab('knowledge', this)">Knowledge</button>
            <button class="tab" onclick="switchTab('abilities', this)">Abilities</button>
        </div>

        <!-- Overview Tab -->
        <div class="tab-content active" id="tab-overview">
            <div class="chart-row">
                <div class="chart-card">
                    <h3><span class="dot" style="background:var(--skill-color)"></span> Top Skills by Importance</h3>
                    <canvas id="chart-skills-overview"></canvas>
                </div>
                <div class="chart-card">
                    <h3><span class="dot" style="background:var(--knowledge-color)"></span> Top Knowledge Areas</h3>
                    <canvas id="chart-knowledge-overview"></canvas>
                </div>
            </div>
            <div class="chart-row">
                <div class="chart-card">
                    <h3><span class="dot" style="background:var(--ability-color)"></span> Top Abilities</h3>
                    <canvas id="chart-abilities-overview"></canvas>
                </div>
                <div class="chart-card">
                    <h3><span class="dot" style="background:var(--task-color)"></span> Task Categories</h3>
                    <canvas id="chart-tasks-overview"></canvas>
                </div>
            </div>
        </div>

        <!-- AI Impact Tab -->
        <div class="tab-content" id="tab-ai-impact">
            <!-- AI Summary -->
            <div class="ai-summary-card">
                <h2>AI Impact Assessment</h2>
                <div class="summary-text" id="ai-summary-text"></div>
                <div class="ai-score-ring">
                    <div>
                        <div class="ring-container">
                            <canvas id="chart-ai-score-ring" width="100" height="100"></canvas>
                            <div class="ring-value" id="ai-ring-value">0</div>
                        </div>
                        <div class="ring-label">Impact Score</div>
                    </div>
                    <div class="ai-metrics">
                        <div class="ai-metric">
                            <div class="val" id="ai-metric-auto" style="color:#FCA5A5">0</div>
                            <div class="lbl">Automatable</div>
                        </div>
                        <div class="ai-metric">
                            <div class="val" id="ai-metric-augment" style="color:#FCD34D">0</div>
                            <div class="lbl">Augmentable</div>
                        </div>
                        <div class="ai-metric">
                            <div class="val" id="ai-metric-human" style="color:#6EE7B7">0</div>
                            <div class="lbl">Human-Essential</div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- AI Impact Distribution Chart -->
            <div class="chart-row">
                <div class="chart-card">
                    <h3><span class="dot" style="background:var(--ai-color)"></span> Task AI Impact Distribution</h3>
                    <canvas id="chart-ai-distribution"></canvas>
                </div>
                <div class="chart-card">
                    <h3><span class="dot" style="background:var(--ai-color)"></span> Tasks by AI Classification</h3>
                    <canvas id="chart-ai-tasks-bar"></canvas>
                </div>
            </div>

            <!-- Strategic Outlook -->
            <div class="outlook-card">
                <h3>Strategic Outlook</h3>
                <p id="ai-outlook-text"></p>
            </div>

            <!-- Recommended AI Agents -->
            <div class="table-card">
                <div class="section-label">Recommended AI Agents for This Role</div>
                <div class="agent-grid" id="ai-agents-grid"></div>
            </div>

            <!-- Recommended AI Skills -->
            <div class="table-card">
                <div class="section-label">AI-Era Skills to Develop</div>
                <div class="ai-skill-grid" id="ai-skills-grid"></div>
            </div>

            <!-- Per-Task AI Analysis Table -->
            <div class="table-card">
                <div class="section-label">Task-Level AI Impact Analysis</div>
                <input type="text" class="search-box" placeholder="Search tasks..." oninput="filterAITasks(this.value)">
                <div style="display:flex; gap:8px; margin-bottom:12px; flex-wrap:wrap;">
                    <button class="badge" style="cursor:pointer; padding:4px 12px;" onclick="filterAIClass('all')">All</button>
                    <button class="badge badge-automate" style="cursor:pointer; padding:4px 12px;" onclick="filterAIClass('automate')">Automate</button>
                    <button class="badge badge-augment" style="cursor:pointer; padding:4px 12px;" onclick="filterAIClass('augment')">Augment</button>
                    <button class="badge badge-human" style="cursor:pointer; padding:4px 12px;" onclick="filterAIClass('human')">Human-Essential</button>
                </div>
                <div id="ai-tasks-table"></div>
            </div>
        </div>

        <!-- Tasks Tab -->
        <div class="tab-content" id="tab-tasks">
            <div class="table-card">
                <h3>All Tasks</h3>
                <input type="text" class="search-box" placeholder="Search tasks..." oninput="filterTasks(this.value)">
                <div id="tasks-table"></div>
            </div>
        </div>

        <!-- Skills Tab -->
        <div class="tab-content" id="tab-skills">
            <div class="chart-card" style="margin-bottom: var(--gap)">
                <h3><span class="dot" style="background:var(--skill-color)"></span> All Skills — Importance Scores</h3>
                <canvas id="chart-skills-full"></canvas>
            </div>
            <div class="table-card">
                <h3>Skill Details</h3>
                <div class="detail-grid" id="skills-grid"></div>
            </div>
        </div>

        <!-- Knowledge Tab -->
        <div class="tab-content" id="tab-knowledge">
            <div class="chart-card" style="margin-bottom: var(--gap)">
                <h3><span class="dot" style="background:var(--knowledge-color)"></span> All Knowledge Areas — Importance Scores</h3>
                <canvas id="chart-knowledge-full"></canvas>
            </div>
            <div class="table-card">
                <h3>Knowledge Details</h3>
                <div class="detail-grid" id="knowledge-grid"></div>
            </div>
        </div>

        <!-- Abilities Tab -->
        <div class="tab-content" id="tab-abilities">
            <div class="chart-card" style="margin-bottom: var(--gap)">
                <h3><span class="dot" style="background:var(--ability-color)"></span> All Abilities — Importance Scores</h3>
                <canvas id="chart-abilities-full"></canvas>
            </div>
            <div class="table-card">
                <h3>Ability Details</h3>
                <div class="detail-grid" id="abilities-grid"></div>
            </div>
        </div>

        <div class="footer">
            Generated {generated} &bull; Data from O*NET Web Services &bull; U.S. Department of Labor
            &bull; AI Impact analysis is indicative and based on task keyword classification
        </div>
    </div>

    <script>
    // ── Embedded Data ──────────────────────────────────────────────────
    const TASKS = {tasks_json};
    const SKILLS = {skills_json};
    const KNOWLEDGE = {knowledge_json};
    const ABILITIES = {abilities_json};
    const AI_IMPACT = {ai_impact_json};

    const COLORS = {{
        skill: '#3B82F6',
        knowledge: '#10B981',
        ability: '#8B5CF6',
        task: '#F59E0B',
        ai: '#EC4899',
        automate: '#EF4444',
        augment: '#F59E0B',
        human: '#10B981',
    }};

    // ── Tab Switching ──────────────────────────────────────────────────
    function switchTab(name, btn) {{
        document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
        document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
        document.getElementById('tab-' + name).classList.add('active');
        btn.classList.add('active');
    }}

    // ── KPIs ───────────────────────────────────────────────────────────
    document.getElementById('kpi-tasks').textContent = TASKS.length;
    document.getElementById('kpi-skills').textContent = SKILLS.length;
    document.getElementById('kpi-knowledge').textContent = KNOWLEDGE.length;
    document.getElementById('kpi-abilities').textContent = ABILITIES.length;
    document.getElementById('kpi-ai-score').textContent = AI_IMPACT.overall_score;
    document.getElementById('kpi-ai-level').textContent = AI_IMPACT.impact_level + ' Impact';

    // ── Chart Helpers ──────────────────────────────────────────────────
    function makeHorizontalBar(canvasId, items, color, maxItems) {{
        const data = items.slice(0, maxItems || items.length);
        const ctx = document.getElementById(canvasId).getContext('2d');
        return new Chart(ctx, {{
            type: 'bar',
            data: {{
                labels: data.map(d => d.name),
                datasets: [{{
                    data: data.map(d => d.score),
                    backgroundColor: color + 'CC',
                    borderColor: color,
                    borderWidth: 1,
                    borderRadius: 4,
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        callbacks: {{
                            label: ctx => 'Importance: ' + ctx.parsed.x.toFixed(0)
                        }}
                    }}
                }},
                scales: {{
                    x: {{
                        beginAtZero: true,
                        max: 100,
                        title: {{ display: true, text: 'Importance Score', font: {{ size: 11 }} }},
                        grid: {{ color: '#f3f4f6' }}
                    }},
                    y: {{
                        ticks: {{ font: {{ size: 11 }} }},
                        grid: {{ display: false }}
                    }}
                }}
            }}
        }});
    }}

    // ── Overview Charts ────────────────────────────────────────────────
    makeHorizontalBar('chart-skills-overview', SKILLS, COLORS.skill, 10);
    makeHorizontalBar('chart-knowledge-overview', KNOWLEDGE, COLORS.knowledge, 10);
    makeHorizontalBar('chart-abilities-overview', ABILITIES, COLORS.ability, 10);

    // Task category doughnut
    (function() {{
        const core = TASKS.filter(t => t.category === 'Core').length;
        const supp = TASKS.filter(t => t.category === 'Supplemental').length;
        const other = TASKS.length - core - supp;
        const labels = [];
        const data = [];
        const bgColors = [];
        if (core > 0) {{ labels.push('Core'); data.push(core); bgColors.push('#3B82F6CC'); }}
        if (supp > 0) {{ labels.push('Supplemental'); data.push(supp); bgColors.push('#F59E0BCC'); }}
        if (other > 0) {{ labels.push('Other'); data.push(other); bgColors.push('#9CA3AFCC'); }}

        const ctx = document.getElementById('chart-tasks-overview').getContext('2d');
        new Chart(ctx, {{
            type: 'doughnut',
            data: {{
                labels: labels,
                datasets: [{{ data: data, backgroundColor: bgColors, borderColor: '#fff', borderWidth: 2 }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                cutout: '55%',
                plugins: {{
                    legend: {{ position: 'bottom', labels: {{ usePointStyle: true, padding: 16 }} }},
                    tooltip: {{
                        callbacks: {{
                            label: ctx => {{
                                const total = ctx.dataset.data.reduce((a,b) => a+b, 0);
                                return ctx.label + ': ' + ctx.parsed + ' (' + ((ctx.parsed/total)*100).toFixed(0) + '%)';
                            }}
                        }}
                    }}
                }}
            }}
        }});
    }})();

    // ── Full Charts ────────────────────────────────────────────────────
    makeHorizontalBar('chart-skills-full', SKILLS, COLORS.skill);
    makeHorizontalBar('chart-knowledge-full', KNOWLEDGE, COLORS.knowledge);
    makeHorizontalBar('chart-abilities-full', ABILITIES, COLORS.ability);

    // ── Detail Grids ───────────────────────────────────────────────────
    function renderGrid(containerId, items, color) {{
        const container = document.getElementById(containerId);
        container.innerHTML = items.map(item => `
            <div class="detail-item">
                <div class="name">${{item.name}}</div>
                <div class="desc">${{item.description}}</div>
                <div class="score-row">
                    <span>Importance:</span>
                    <div class="score-bar" style="flex:1">
                        <div class="score-fill" style="width:${{item.score}}%;background:${{color}}"></div>
                    </div>
                    <strong>${{item.score.toFixed(0)}}</strong>
                </div>
            </div>
        `).join('');
    }}

    renderGrid('skills-grid', SKILLS, COLORS.skill);
    renderGrid('knowledge-grid', KNOWLEDGE, COLORS.knowledge);
    renderGrid('abilities-grid', ABILITIES, COLORS.ability);

    // ── Tasks Table ────────────────────────────────────────────────────
    let taskSortCol = 'score';
    let taskSortDir = 'desc';
    let taskFilter = '';

    function renderTasks() {{
        let data = TASKS.filter(t =>
            taskFilter === '' || t.statement.toLowerCase().includes(taskFilter.toLowerCase())
        );

        data.sort((a, b) => {{
            let av = a[taskSortCol], bv = b[taskSortCol];
            if (typeof av === 'string') av = av.toLowerCase();
            if (typeof bv === 'string') bv = bv.toLowerCase();
            const cmp = av < bv ? -1 : av > bv ? 1 : 0;
            return taskSortDir === 'asc' ? cmp : -cmp;
        }});

        const arrow = col => taskSortCol === col ? (taskSortDir === 'asc' ? ' ▲' : ' ▼') : '';
        let html = '<table>';
        html += '<thead><tr>';
        html += '<th onclick="sortTasks(\\'statement\\')">Task' + arrow('statement') + '</th>';
        html += '<th onclick="sortTasks(\\'category\\')" style="width:120px">Category' + arrow('category') + '</th>';
        html += '<th onclick="sortTasks(\\'score\\')" style="width:140px">Importance' + arrow('score') + '</th>';
        html += '</tr></thead><tbody>';

        data.forEach(t => {{
            const badgeClass = t.category === 'Core' ? 'badge-core' : t.category === 'Supplemental' ? 'badge-supplemental' : '';
            html += '<tr>';
            html += '<td>' + t.statement + '</td>';
            html += '<td><span class="badge ' + badgeClass + '">' + (t.category || '—') + '</span></td>';
            html += '<td><div class="score-row"><div class="score-bar" style="flex:1"><div class="score-fill" style="width:' + t.score + '%;background:' + COLORS.task + '"></div></div><span>' + t.score.toFixed(0) + '</span></div></td>';
            html += '</tr>';
        }});

        html += '</tbody></table>';
        if (data.length === 0) html = '<p style="text-align:center;color:var(--text-secondary);padding:20px">No tasks match your search.</p>';
        document.getElementById('tasks-table').innerHTML = html;
    }}

    function sortTasks(col) {{
        if (taskSortCol === col) taskSortDir = taskSortDir === 'asc' ? 'desc' : 'asc';
        else {{ taskSortCol = col; taskSortDir = col === 'statement' ? 'asc' : 'desc'; }}
        renderTasks();
    }}

    function filterTasks(val) {{
        taskFilter = val;
        renderTasks();
    }}

    renderTasks();

    // ── AI Impact Tab ──────────────────────────────────────────────────

    // Summary text and outlook
    document.getElementById('ai-summary-text').innerHTML = AI_IMPACT.role_summary;
    document.getElementById('ai-outlook-text').textContent = AI_IMPACT.outlook;

    // Score ring (mini doughnut)
    (function() {{
        const score = AI_IMPACT.overall_score;
        document.getElementById('ai-ring-value').textContent = score;
        const ctx = document.getElementById('chart-ai-score-ring').getContext('2d');
        new Chart(ctx, {{
            type: 'doughnut',
            data: {{
                datasets: [{{
                    data: [score, 100 - score],
                    backgroundColor: [AI_IMPACT.impact_color + 'DD', 'rgba(255,255,255,0.15)'],
                    borderWidth: 0,
                }}]
            }},
            options: {{
                responsive: false,
                cutout: '75%',
                plugins: {{ legend: {{ display: false }}, tooltip: {{ enabled: false }} }},
                animation: {{ animateRotate: true }}
            }}
        }});
    }})();

    // Metrics
    const dist = AI_IMPACT.distribution;
    document.getElementById('ai-metric-auto').textContent = dist.automate;
    document.getElementById('ai-metric-augment').textContent = dist.augment;
    document.getElementById('ai-metric-human').textContent = dist.human;

    // AI Distribution doughnut
    (function() {{
        const d = AI_IMPACT.distribution;
        const ctx = document.getElementById('chart-ai-distribution').getContext('2d');
        new Chart(ctx, {{
            type: 'doughnut',
            data: {{
                labels: ['AI Can Automate', 'AI Can Augment', 'Human-Essential'],
                datasets: [{{
                    data: [d.automate, d.augment, d.human],
                    backgroundColor: [COLORS.automate + 'CC', COLORS.augment + 'CC', COLORS.human + 'CC'],
                    borderColor: '#fff',
                    borderWidth: 2,
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                cutout: '50%',
                plugins: {{
                    legend: {{
                        position: 'bottom',
                        labels: {{ usePointStyle: true, padding: 16, font: {{ size: 12 }} }}
                    }},
                    tooltip: {{
                        callbacks: {{
                            label: ctx => {{
                                const total = ctx.dataset.data.reduce((a,b) => a+b, 0);
                                const pct = total > 0 ? ((ctx.parsed / total) * 100).toFixed(0) : 0;
                                return ctx.label + ': ' + ctx.parsed + ' tasks (' + pct + '%)';
                            }}
                        }}
                    }}
                }}
            }}
        }});
    }})();

    // AI tasks horizontal bar — importance by classification
    (function() {{
        const ta = AI_IMPACT.task_analysis;
        const autoTasks = ta.filter(t => t.classification === 'automate').sort((a,b) => b.importance - a.importance).slice(0, 8);
        const augmentTasks = ta.filter(t => t.classification === 'augment').sort((a,b) => b.importance - a.importance).slice(0, 8);
        const humanTasks = ta.filter(t => t.classification === 'human').sort((a,b) => b.importance - a.importance).slice(0, 8);

        const truncate = (s, n) => s.length > n ? s.substring(0, n) + '...' : s;
        const all = [...autoTasks, ...augmentTasks, ...humanTasks].sort((a,b) => b.importance - a.importance).slice(0, 15);

        const ctx = document.getElementById('chart-ai-tasks-bar').getContext('2d');
        new Chart(ctx, {{
            type: 'bar',
            data: {{
                labels: all.map(t => truncate(t.statement, 55)),
                datasets: [{{
                    data: all.map(t => t.importance),
                    backgroundColor: all.map(t =>
                        t.classification === 'automate' ? COLORS.automate + 'CC' :
                        t.classification === 'human' ? COLORS.human + 'CC' : COLORS.augment + 'CC'
                    ),
                    borderColor: all.map(t =>
                        t.classification === 'automate' ? COLORS.automate :
                        t.classification === 'human' ? COLORS.human : COLORS.augment
                    ),
                    borderWidth: 1,
                    borderRadius: 4,
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        callbacks: {{
                            title: ctx => {{
                                const idx = ctx[0].dataIndex;
                                return all[idx].statement;
                            }},
                            label: ctx => {{
                                const idx = ctx.dataIndex;
                                const t = all[idx];
                                return ['Importance: ' + t.importance.toFixed(0),
                                        'Classification: ' + t.classification.charAt(0).toUpperCase() + t.classification.slice(1)];
                            }}
                        }}
                    }}
                }},
                scales: {{
                    x: {{ beginAtZero: true, max: 100, grid: {{ color: '#f3f4f6' }} }},
                    y: {{ ticks: {{ font: {{ size: 10 }} }}, grid: {{ display: false }} }}
                }}
            }}
        }});
    }})();

    // AI Agents grid
    (function() {{
        const grid = document.getElementById('ai-agents-grid');
        grid.innerHTML = AI_IMPACT.agents.map(a => `
            <div class="agent-card">
                <div class="agent-name">${{a.name}}</div>
                <div class="agent-desc">${{a.desc}}</div>
                <div class="agent-value"><strong>Business Value:</strong> ${{a.business_value}}</div>
                <div class="relevance-bar">
                    <span>Relevance:</span>
                    <div class="score-bar" style="flex:1">
                        <div class="score-fill" style="width:${{a.relevance_score}}%;background:${{COLORS.ai}}"></div>
                    </div>
                    <strong>${{a.relevance_score}}%</strong>
                </div>
            </div>
        `).join('');
    }})();

    // AI Skills grid
    (function() {{
        const grid = document.getElementById('ai-skills-grid');
        grid.innerHTML = AI_IMPACT.ai_skills.map(s => `
            <div class="ai-skill-card">
                <div class="skill-name">
                    ${{s.name}}
                    <span class="badge badge-${{s.priority.toLowerCase()}}">${{s.priority}}</span>
                </div>
                <div class="skill-desc">${{s.desc}}</div>
            </div>
        `).join('');
    }})();

    // AI Tasks table
    let aiTaskFilter = '';
    let aiClassFilter = 'all';
    let aiSortCol = 'importance';
    let aiSortDir = 'desc';

    function renderAITasks() {{
        let data = AI_IMPACT.task_analysis.filter(t => {{
            if (aiClassFilter !== 'all' && t.classification !== aiClassFilter) return false;
            if (aiTaskFilter && !t.statement.toLowerCase().includes(aiTaskFilter.toLowerCase())) return false;
            return true;
        }});

        data.sort((a, b) => {{
            let av = a[aiSortCol], bv = b[aiSortCol];
            if (typeof av === 'string') av = av.toLowerCase();
            if (typeof bv === 'string') bv = bv.toLowerCase();
            const cmp = av < bv ? -1 : av > bv ? 1 : 0;
            return aiSortDir === 'asc' ? cmp : -cmp;
        }});

        const arrow = col => aiSortCol === col ? (aiSortDir === 'asc' ? ' ▲' : ' ▼') : '';
        let html = '<table>';
        html += '<thead><tr>';
        html += '<th onclick="sortAITasks(\\'statement\\')">Task' + arrow('statement') + '</th>';
        html += '<th onclick="sortAITasks(\\'classification\\')" style="width:130px">AI Impact' + arrow('classification') + '</th>';
        html += '<th onclick="sortAITasks(\\'confidence\\')" style="width:110px">Confidence' + arrow('confidence') + '</th>';
        html += '<th onclick="sortAITasks(\\'importance\\')" style="width:120px">Importance' + arrow('importance') + '</th>';
        html += '</tr></thead><tbody>';

        data.forEach(t => {{
            const clsLabel = t.classification === 'automate' ? 'Automate' : t.classification === 'human' ? 'Human-Essential' : 'Augment';
            const clsBadge = 'badge-' + t.classification;
            const color = COLORS[t.classification];
            html += '<tr>';
            html += '<td title="' + t.rationale.replace(/"/g, '&quot;') + '">' + t.statement + '</td>';
            html += '<td><span class="badge ' + clsBadge + '">' + clsLabel + '</span></td>';
            html += '<td><div class="score-row"><div class="score-bar" style="flex:1"><div class="score-fill" style="width:' + t.confidence + '%;background:#9CA3AF"></div></div><span>' + t.confidence + '%</span></div></td>';
            html += '<td><div class="score-row"><div class="score-bar" style="flex:1"><div class="score-fill" style="width:' + t.importance + '%;background:' + color + '"></div></div><span>' + t.importance.toFixed(0) + '</span></div></td>';
            html += '</tr>';
        }});

        html += '</tbody></table>';
        if (data.length === 0) html = '<p style="text-align:center;color:var(--text-secondary);padding:20px">No tasks match your filters.</p>';
        document.getElementById('ai-tasks-table').innerHTML = html;
    }}

    function sortAITasks(col) {{
        if (aiSortCol === col) aiSortDir = aiSortDir === 'asc' ? 'desc' : 'asc';
        else {{ aiSortCol = col; aiSortDir = col === 'statement' ? 'asc' : 'desc'; }}
        renderAITasks();
    }}

    function filterAITasks(val) {{
        aiTaskFilter = val;
        renderAITasks();
    }}

    function filterAIClass(cls) {{
        aiClassFilter = cls;
        renderAITasks();
    }}

    renderAITasks();
    </script>
</body>
</html>""")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="O*NET Occupation Explorer — search occupations and generate an interactive dashboard with AI impact analysis.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python onet_explorer.py "software developer"
              python onet_explorer.py "registered nurse" --output nurse_dashboard.html

            Environment variables:
              ONET_API_KEY   Your O*NET Web Services API key

            Register and generate a key at: https://services.onetcenter.org/
        """)
    )
    parser.add_argument("keyword", help="Occupation keyword to search (e.g. 'data scientist')")
    parser.add_argument("--api-key", default=os.environ.get("ONET_API_KEY", ""),
                        help="O*NET API key (or set ONET_API_KEY env var)")
    parser.add_argument("--output", "-o", default="",
                        help="Output HTML filename (default: onet_<occupation_code>.html)")
    args = parser.parse_args()

    # Validate credentials
    api_key = args.api_key
    if not api_key:
        print("─" * 60)
        print("O*NET API key required.")
        print("Set ONET_API_KEY environment variable,")
        print("or pass --api-key argument.")
        print("Register and generate a key at: https://services.onetcenter.org/")
        print("─" * 60)
        sys.exit(1)

    # Search
    print(f"\nSearching O*NET for: \"{args.keyword}\"...")
    results = search_occupations(args.keyword, api_key)
    if not results:
        print("No occupations found. Try a different keyword.")
        sys.exit(0)

    # Display results
    print(f"\nFound {len(results)} occupation(s):\n")
    for i, occ in enumerate(results, 1):
        print(f"  {i:>3}. [{occ['code']}]  {occ['title']}")

    # Select
    if len(results) == 1:
        choice = 0
    else:
        print()
        while True:
            try:
                raw = input(f"Select an occupation (1-{len(results)}): ").strip()
                choice = int(raw) - 1
                if 0 <= choice < len(results):
                    break
                print(f"  Enter a number between 1 and {len(results)}.")
            except (ValueError, EOFError):
                print("  Enter a valid number.")

    selected = results[choice]
    code = selected["code"]
    print(f"\nFetching data for: {selected['title']} ({code})...")

    # Fetch all data
    summary = get_occupation_summary(code, api_key)
    print("  ✓ Summary")

    tasks = get_occupation_tasks(code, api_key)
    print(f"  ✓ Tasks ({len(tasks)})")

    skills = get_occupation_elements(code, "skills", api_key)
    print(f"  ✓ Skills ({len(skills)})")

    knowledge = get_occupation_elements(code, "knowledge", api_key)
    print(f"  ✓ Knowledge ({len(knowledge)})")

    abilities = get_occupation_elements(code, "abilities", api_key)
    print(f"  ✓ Abilities ({len(abilities)})")

    # AI Impact Analysis
    print("  ⚡ Analyzing AI impact...")
    ai_impact = analyze_ai_impact(summary, tasks, skills, knowledge, abilities)
    print(f"  ✓ AI Impact: {ai_impact['impact_level']} (score: {ai_impact['overall_score']})")
    print(f"    Tasks: {ai_impact['distribution']['automate']} automatable, "
          f"{ai_impact['distribution']['augment']} augmentable, "
          f"{ai_impact['distribution']['human']} human-essential")
    print(f"    Recommended agents: {len(ai_impact['agents'])}")

    # Generate dashboard
    dashboard_html = generate_dashboard(summary, tasks, skills, knowledge, abilities, ai_impact)

    # Write output
    safe_code = code.replace(".", "_").replace("-", "_")
    output_file = args.output or f"onet_{safe_code}.html"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(dashboard_html)

    print(f"\n{'═' * 60}")
    print(f"  Dashboard saved: {output_file}")
    print(f"  Open in any browser to explore the data interactively.")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
