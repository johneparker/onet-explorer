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
    """Fetch the occupation summary/description including bright outlook."""
    data = make_request(
        f"online/occupations/{quote(code, safe='')}",
        api_key
    )
    return {
        "title": data.get("title", ""),
        "description": data.get("description", ""),
        "code": data.get("code", code),
        "bright_outlook": data.get("bright_outlook", []),
        "is_bright_outlook": data.get("tags", {}).get("bright_outlook", False),
        "sample_titles": data.get("sample_of_reported_titles", []),
    }


def get_education_requirements(code: str, api_key: str) -> list:
    """Fetch education requirements for an occupation."""
    data = make_request(
        f"online/occupations/{quote(code, safe='')}/details/education",
        api_key
    )
    return data.get("response", [])


def get_job_zone(code: str, api_key: str) -> dict:
    """Fetch job zone info (preparation level, experience, training)."""
    data = make_request(
        f"online/occupations/{quote(code, safe='')}/details/job_zone",
        api_key
    )
    return {
        "code": data.get("code", 0),
        "title": data.get("title", ""),
        "education": data.get("education", ""),
        "experience": data.get("related_experience", ""),
        "training": data.get("job_training", ""),
    }


def get_hot_technologies(code: str, api_key: str) -> list:
    """Fetch hot/in-demand technologies for an occupation."""
    raw = _fetch_all_pages(
        f"online/occupations/{quote(code, safe='')}/hot_technology",
        api_key, "example"
    )
    techs = []
    for t in raw:
        techs.append({
            "title": t.get("title", ""),
            "hot_technology": t.get("hot_technology", False),
            "in_demand": t.get("in_demand", False),
            "percentage": t.get("percentage", 0),
        })
    return sorted(techs, key=lambda x: x["percentage"], reverse=True)


def get_occupation_industries(code: str, api_key: str) -> list:
    """Find all industries that employ this occupation with employment data.

    Scans all 21 NAICS industry sectors via the O*NET industries endpoint
    and returns industries where this occupation appears, with employment
    distribution, growth projections, and estimated job openings.
    """
    # Get list of all industries (returns a plain list)
    industries_list = make_request("online/industries/", api_key)
    if isinstance(industries_list, dict):
        industries_list = industries_list.get("industry", industries_list.get("industries", []))

    results = []
    for ind in industries_list:
        ind_code = ind.get("code", "")
        ind_title = ind.get("title", "")

        # Fetch occupations in this industry (large page to avoid pagination overhead)
        try:
            data = make_request(
                f"online/industries/{ind_code}",
                api_key,
                {"start": 1, "end": 500}
            )
            all_occs = data.get("occupation", []) if isinstance(data, dict) else []
        except RuntimeError:
            continue

        # Search for our occupation in this industry
        for occ in all_occs:
            if occ.get("code") == code:
                pct = occ.get("percent_employed", 0)
                openings_total = occ.get("projected_openings", 0)
                results.append({
                    "industry_code": ind_code,
                    "industry": ind_title,
                    "percent_employed": pct,
                    "projected_growth": occ.get("projected_growth", "N/A"),
                    "projected_openings": openings_total,
                    "estimated_industry_openings": int(openings_total * pct / 100) if pct and openings_total else 0,
                    "bright_outlook": occ.get("tags", {}).get("bright_outlook", False),
                })
                break

    return sorted(results, key=lambda x: x["percent_employed"], reverse=True)


# ─── BLS OEWS Employment Data ────────────────────────────────────────────────

BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# All 50 states + DC with FIPS codes
_STATE_FIPS = {
    "Alabama": "01", "Alaska": "02", "Arizona": "04", "Arkansas": "05",
    "California": "06", "Colorado": "08", "Connecticut": "09", "Delaware": "10",
    "District of Columbia": "11", "Florida": "12", "Georgia": "13", "Hawaii": "15",
    "Idaho": "16", "Illinois": "17", "Indiana": "18", "Iowa": "19",
    "Kansas": "20", "Kentucky": "21", "Louisiana": "22", "Maine": "23",
    "Maryland": "24", "Massachusetts": "25", "Michigan": "26", "Minnesota": "27",
    "Mississippi": "28", "Missouri": "29", "Montana": "30", "Nebraska": "31",
    "Nevada": "32", "New Hampshire": "33", "New Jersey": "34", "New Mexico": "35",
    "New York": "36", "North Carolina": "37", "North Dakota": "38", "Ohio": "39",
    "Oklahoma": "40", "Oregon": "41", "Pennsylvania": "42", "Rhode Island": "44",
    "South Carolina": "45", "South Dakota": "46", "Tennessee": "47", "Texas": "48",
    "Utah": "49", "Vermont": "50", "Virginia": "51", "Washington": "53",
    "West Virginia": "54", "Wisconsin": "55", "Wyoming": "56",
}

# Major NAICS industry sectors (3-digit codes that work with BLS OEWS)
_BLS_INDUSTRIES = {
    "111000": "Crop Production",
    "112000": "Animal Production & Aquaculture",
    "113000": "Forestry & Logging",
    "211000": "Oil & Gas Extraction",
    "212000": "Mining (except Oil & Gas)",
    "221000": "Utilities",
    "236000": "Construction of Buildings",
    "237000": "Heavy & Civil Engineering Construction",
    "238000": "Specialty Trade Contractors",
    "311000": "Food Manufacturing",
    "312000": "Beverage & Tobacco Manufacturing",
    "313000": "Textile Mills",
    "315000": "Apparel Manufacturing",
    "321000": "Wood Product Manufacturing",
    "322000": "Paper Manufacturing",
    "323000": "Printing & Related Support",
    "324000": "Petroleum & Coal Products",
    "325000": "Chemical Manufacturing",
    "326000": "Plastics & Rubber Products",
    "327000": "Nonmetallic Mineral Products",
    "331000": "Primary Metal Manufacturing",
    "332000": "Fabricated Metal Products",
    "333000": "Machinery Manufacturing",
    "334000": "Computer & Electronic Products",
    "335000": "Electrical Equipment & Appliances",
    "336000": "Transportation Equipment",
    "337000": "Furniture & Related Products",
    "339000": "Miscellaneous Manufacturing",
    "423000": "Merchant Wholesalers, Durable Goods",
    "424000": "Merchant Wholesalers, Nondurable Goods",
    "425000": "Wholesale Electronic Markets",
    "441000": "Motor Vehicle & Parts Dealers",
    "445000": "Food & Beverage Stores",
    "452000": "General Merchandise Stores",
    "481000": "Air Transportation",
    "482000": "Rail Transportation",
    "484000": "Truck Transportation",
    "486000": "Pipeline Transportation",
    "488000": "Support Activities for Transportation",
    "491000": "Postal Service",
    "492000": "Couriers & Messengers",
    "493000": "Warehousing & Storage",
    "511000": "Publishing Industries",
    "512000": "Motion Picture & Sound Recording",
    "515000": "Broadcasting",
    "517000": "Telecommunications",
    "518000": "Computing Infrastructure Providers & Data Processing",
    "519000": "Web Search Portals & Other Information Services",
    "521000": "Monetary Authorities – Central Bank",
    "522000": "Credit Intermediation & Related",
    "523000": "Securities & Financial Investments",
    "524000": "Insurance Carriers & Related",
    "525000": "Funds, Trusts & Other Financial Vehicles",
    "531000": "Real Estate",
    "532000": "Rental & Leasing Services",
    "541000": "Professional, Scientific & Technical Services",
    "551000": "Management of Companies & Enterprises",
    "561000": "Administrative & Support Services",
    "562000": "Waste Management & Remediation",
    "611000": "Educational Services",
    "621000": "Ambulatory Health Care Services",
    "622000": "Hospitals",
    "623000": "Nursing & Residential Care Facilities",
    "624000": "Social Assistance",
    "711000": "Performing Arts & Spectator Sports",
    "712000": "Museums & Historical Sites",
    "713000": "Amusement, Gambling & Recreation",
    "721000": "Accommodation",
    "722000": "Food Services & Drinking Places",
    "811000": "Repair & Maintenance",
    "812000": "Personal & Laundry Services",
    "813000": "Religious, Civic & Professional Organizations",
    "921000": "Executive & Legislative Offices",
    "922000": "Justice, Public Order & Safety",
    "923000": "Administration of Human Resource Programs",
    "924000": "Administration of Environmental Programs",
    "925000": "Community & Housing Programs",
    "926000": "Administration of Economic Programs",
    "928000": "National Security & International Affairs",
    "999100": "Federal Government, Civilian",
    "999200": "State Government",
    "999300": "Local Government",
}


def _onet_to_bls_soc(onet_code: str) -> str:
    """Convert O*NET code (e.g., '15-1252.00') to BLS SOC (e.g., '151252')."""
    base = onet_code.split(".")[0]  # Remove .00 suffix
    return base.replace("-", "")


def _bls_post(series_ids: list, bls_api_key: str = "") -> dict:
    """POST to BLS v2 API with up to 50 series IDs. Returns dict of series_id -> value."""
    payload = json.dumps({
        "seriesid": series_ids,
        "startyear": "2023",
        "endyear": "2024",
    })
    if bls_api_key:
        payload_dict = json.loads(payload)
        payload_dict["registrationkey"] = bls_api_key
        payload = json.dumps(payload_dict)

    req = Request(BLS_API_URL, data=payload.encode("utf-8"))
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return {}

    results = {}
    if data.get("status") == "REQUEST_NOT_PROCESSED":
        # Rate limited or other processing error — return empty
        return {}
    if data.get("status") == "REQUEST_SUCCEEDED":
        for series in data.get("Results", {}).get("series", []):
            sid = series.get("seriesID", "")
            series_data = series.get("data", [])
            if series_data:
                # Get most recent annual data (period M13 = annual mean)
                annual = [d for d in series_data if d.get("period") == "M13"]
                if annual:
                    val = annual[0].get("value", "0")
                    try:
                        results[sid] = int(float(val.replace(",", "")))
                    except (ValueError, TypeError):
                        pass
                elif series_data:
                    # Fall back to most recent available data point
                    val = series_data[0].get("value", "0")
                    try:
                        results[sid] = int(float(val.replace(",", "")))
                    except (ValueError, TypeError):
                        pass
    return results


def get_bls_employment_by_state(onet_code: str, bls_api_key: str = "") -> list:
    """Fetch employment counts for an occupation by state from BLS OEWS.

    Returns list of dicts: {state, fips, employment}
    sorted by employment descending.
    """
    soc = _onet_to_bls_soc(onet_code)

    # Build series IDs: OEUS{FIPS2}00000000000{SOC6}01
    states_list = list(_STATE_FIPS.items())
    series_map = {}
    for state_name, fips in states_list:
        sid = f"OEUS{fips}00000000000{soc}01"
        series_map[sid] = {"state": state_name, "fips": fips}

    # BLS allows max 50 series per request — need 2 batches for 51 states
    all_sids = list(series_map.keys())
    batch_size = 50
    bls_results = {}
    for i in range(0, len(all_sids), batch_size):
        batch = all_sids[i:i + batch_size]
        batch_results = _bls_post(batch, bls_api_key)
        bls_results.update(batch_results)

    # Build results
    results = []
    for sid, info in series_map.items():
        emp = bls_results.get(sid)
        if emp is not None and emp > 0:
            results.append({
                "state": info["state"],
                "fips": info["fips"],
                "employment": emp,
            })

    return sorted(results, key=lambda x: x["employment"], reverse=True)


def get_bls_employment_by_industry(onet_code: str, bls_api_key: str = "") -> list:
    """Fetch national employment counts for an occupation by industry from BLS OEWS.

    Returns list of dicts: {industry_code, industry, employment}
    sorted by employment descending.
    """
    soc = _onet_to_bls_soc(onet_code)

    # Build series IDs: OEUN0000000{NAICS6}{SOC6}01
    series_map = {}
    for naics, name in _BLS_INDUSTRIES.items():
        sid = f"OEUN0000000{naics}{soc}01"
        series_map[sid] = {"industry_code": naics, "industry": name}

    # Batch requests (50 per batch)
    all_sids = list(series_map.keys())
    batch_size = 50
    bls_results = {}
    for i in range(0, len(all_sids), batch_size):
        batch = all_sids[i:i + batch_size]
        batch_results = _bls_post(batch, bls_api_key)
        bls_results.update(batch_results)

    # Build results
    results = []
    for sid, info in series_map.items():
        emp = bls_results.get(sid)
        if emp is not None and emp > 0:
            results.append({
                "industry_code": info["industry_code"],
                "industry": info["industry"],
                "employment": emp,
            })

    return sorted(results, key=lambda x: x["employment"], reverse=True)


def get_bls_national_employment(onet_code: str, bls_api_key: str = "") -> int:
    """Fetch the national total employment for an occupation from BLS OEWS."""
    soc = _onet_to_bls_soc(onet_code)
    # Format: OE(2) + U(1) + N(1) + area_code(7=0000000) + industry(6=000000) + SOC(6) + datatype(2=01) = 25 chars
    sid = f"OEUN0000000000000{soc}01"
    results = _bls_post([sid], bls_api_key)
    return results.get(sid, 0)


# ─── AI Impact Analysis Engine ───────────────────────────────────────────────
#
# Five-Element Business Impact Scoring Model
# Each task is scored 0-9 on five dimensions of AI impact potential.
# Element averages across all tasks produce the occupation-level scores.
#
# Elements:
#   1. Efficiency & Time Savings — AI reduces duration, eliminates steps, accelerates throughput
#   2. Quality & Accuracy — AI improves consistency, reduces errors, increases precision
#   3. Cost Impact — AI reduces labor cost, reallocates resources, lowers operational spend
#   4. Revenue & Innovation Potential — AI unlocks new revenue, services, differentiation
#   5. Service & Stakeholder Satisfaction — AI improves CX, employee experience, responsiveness

# ── Element Keyword Dictionaries ──
# Each element has "strong" patterns (contribute 2.0 points) and "moderate" patterns (1.0 point).
# A task's raw score = sum of weights; element score = min(9, round(raw)).

_EFFICIENCY_KEYWORDS = {
    "strong": [
        r"\bschedul\w*", r"\btrack\w*", r"\bmonitor\w*", r"\brecord\w*",
        r"\bcompil\w*", r"\bsort\w*", r"\bdata.?entry", r"\btranscri\w*",
        r"\bcalculat\w*", r"\btabulat\w*", r"\bprocess\w*", r"\brout\w*",
        r"\bgenerat\w* report", r"\bupdat\w* (record|database|system|file|log)",
        r"\barchiv\w*", r"\bcatalog\w*", r"\bindex\w*", r"\bformat\w*",
        r"\bautomat\w*", r"\brepetitiv\w*", r"\bstreamlin\w*", r"\bexpedi\w*",
    ],
    "moderate": [
        r"\banalyz\w*", r"\breview\w*", r"\bcoordinat\w*", r"\bplan\w*",
        r"\borganiz\w*", r"\bprioritiz\w*", r"\bsummariz\w*", r"\bfile\w*",
        r"\binventory", r"\bbookkeep\w*", r"\bpayroll", r"\binvoic\w*",
        r"\blog\w*\b", r"\bverif\w*", r"\bcheck\w*",
    ],
}

_QUALITY_KEYWORDS = {
    "strong": [
        r"\bverif\w*", r"\bvalidat\w*", r"\binspect\w*", r"\baudit\w*",
        r"\btest\w*", r"\bquality\w*", r"\baccura\w*", r"\bmeasur\w*",
        r"\bdiagnos\w*", r"\bdetect\w*", r"\bcheck\w*", r"\bcompl\w*iance",
        r"\bstandard\w*", r"\bcertif\w*", r"\bcalibrat\w*", r"\bprecis\w*",
        r"\bconsisten\w*", r"\berror\w*", r"\bdefect\w*",
    ],
    "moderate": [
        r"\banalyz\w*", r"\bevaluat\w*", r"\bassess\w*", r"\bresearch\w*",
        r"\bexamin\w*", r"\breview\w*", r"\bmonitor\w*", r"\binterpret\w*",
        r"\bdesign\w*", r"\bdevelop\w*", r"\bmodel\w*", r"\bforecast\w*",
        r"\bstatistic\w*", r"\boptimiz\w*",
    ],
}

_COST_KEYWORDS = {
    "strong": [
        r"\bprocess\w*", r"\bautomat\w*", r"\brout\w*", r"\brepetitiv\w*",
        r"\bmanual\w*", r"\badministrat\w*", r"\bclerical\w*", r"\bdata.?entry",
        r"\bfile\w*", r"\brecord\w*", r"\bschedul\w*", r"\bbilling\w*",
        r"\binvoic\w*", r"\bpayroll", r"\binventory", r"\bprocur\w*",
        r"\bbookkeep\w*", r"\bbudget\w*", r"\bcost\w*",
    ],
    "moderate": [
        r"\banalyz\w*", r"\bresearch\w*", r"\breview\w*", r"\bplan\w*",
        r"\bdevelop\w*", r"\bimplement\w*", r"\bcoordinat\w*",
        r"\bcompil\w*", r"\btrack\w*", r"\bmonitor\w*", r"\breport\w*",
        r"\bmaintain\w*", r"\bupdat\w*",
    ],
}

_REVENUE_KEYWORDS = {
    "strong": [
        r"\bdevelop\w*", r"\bdesign\w*", r"\binnovat\w*", r"\bcreat\w*",
        r"\bmarket\w*", r"\bproduct\w*", r"\bstrateg\w*", r"\brevenue\w*",
        r"\bgrowth\w*", r"\bopportunit\w*", r"\bresearch\w*", r"\bprototyp\w*",
        r"\bexperiment\w*", r"\boptimiz\w*", r"\bcompetitiv\w*",
        r"\bcustomer\w* (acqui|retain|segment|experience)",
        r"\bsales\w*", r"\bbusiness\w* develop",
    ],
    "moderate": [
        r"\banalyz\w*", r"\bplan\w*", r"\bevaluat\w*", r"\bimplement\w*",
        r"\bcommunicat\w*", r"\bpresent\w*", r"\bforecast\w*",
        r"\bidentif\w*", r"\bpropos\w*", r"\brecommend\w*",
        r"\bprogram\w*", r"\bcode\w*", r"\btest\w*",
    ],
}

_SERVICE_KEYWORDS = {
    "strong": [
        r"\bcustomer\w*", r"\bclient\w*", r"\bpatient\w*", r"\bcommunicat\w*",
        r"\brespond\w*", r"\bservice\w*", r"\bsupport\w*", r"\bconsult\w*",
        r"\badvise\w*", r"\bpresent\w*", r"\bstakeholder\w*", r"\brelationship\w*",
        r"\bengage\w*", r"\bsatisfact\w*", r"\bfeedback\w*",
        r"\bresolv\w* (issue|complaint|problem|concern)",
    ],
    "moderate": [
        r"\bcoordinat\w*", r"\bcollaborat\w*", r"\breview\w*", r"\bevaluat\w*",
        r"\breport\w*", r"\bdeliver\w*", r"\btrain\w*", r"\beduca\w*",
        r"\binform\w*", r"\bnotif\w*", r"\bnegotiat\w*",
        r"\bmediat\w*", r"\bmentor\w*", r"\bcoach\w*",
    ],
}

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


def _score_element(statement: str, keyword_dict: dict) -> int:
    """Score a task 0-9 on a single business impact element.

    Uses strong keywords (weight 2.0 each) and moderate keywords (weight 1.0 each).
    Raw weighted sum is clamped to 0-9.
    """
    strong_hits = _match_keywords(statement, keyword_dict["strong"])
    moderate_hits = _match_keywords(statement, keyword_dict["moderate"])
    raw = strong_hits * 2.0 + moderate_hits * 1.0
    return min(9, max(0, round(raw)))


def score_task_elements(statement: str) -> dict:
    """Score a single task on five business impact elements (each 0-9).

    Returns dict with element scores and derived classification.
    """
    efficiency = _score_element(statement, _EFFICIENCY_KEYWORDS)
    quality = _score_element(statement, _QUALITY_KEYWORDS)
    cost = _score_element(statement, _COST_KEYWORDS)
    revenue = _score_element(statement, _REVENUE_KEYWORDS)
    service = _score_element(statement, _SERVICE_KEYWORDS)

    avg = (efficiency + quality + cost + revenue + service) / 5.0

    # Derive classification from average for backward compatibility
    if avg >= 5.0:
        classification = "automate"
    elif avg >= 2.5:
        classification = "augment"
    else:
        classification = "human"

    return {
        "efficiency": efficiency,
        "quality": quality,
        "cost": cost,
        "revenue": revenue,
        "service": service,
        "avg_score": round(avg, 1),
        "classification": classification,
    }


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
    """Produce a five-element AI business impact analysis for an occupation.

    Each task is scored 0-9 on five dimensions. Element averages across all
    tasks produce the occupation-level element scores.

    Returns a dict with:
        role_summary    — narrative description of AI's impact on the role
        task_analysis   — per-task scoring list with five element scores
        element_scores  — {efficiency, quality, cost, revenue, service} averages
        distribution    — {automate, augment, human} counts (derived)
        overall_score   — 0-100 composite AI impact score
        agents          — ranked list of recommended AI agents
        ai_skills       — recommended skills for AI-era readiness
        outlook         — strategic outlook narrative
    """
    # Score every task on five elements
    task_analysis = []
    for t in tasks:
        scores = score_task_elements(t["statement"])
        task_analysis.append({
            "statement": t["statement"],
            "importance": t["score"]["value"] if isinstance(t["score"], dict) else t["score"],
            "category": t.get("category", ""),
            **scores,
        })

    n_total = max(len(task_analysis), 1)

    # Compute element averages
    elem_keys = ["efficiency", "quality", "cost", "revenue", "service"]
    element_scores = {}
    for key in elem_keys:
        avg = sum(t[key] for t in task_analysis) / n_total if task_analysis else 0
        element_scores[key] = round(avg, 1)

    # Overall score: mean of five element averages, scaled to 0-100
    avg_of_elements = sum(element_scores.values()) / 5.0 if element_scores else 0
    overall_score = min(95, max(5, int((avg_of_elements / 9.0) * 100)))

    # Distribution counts (derived from avg_score per task)
    n_auto = sum(1 for t in task_analysis if t["classification"] == "automate")
    n_augment = sum(1 for t in task_analysis if t["classification"] == "augment")
    n_human = sum(1 for t in task_analysis if t["classification"] == "human")
    distribution = {"automate": n_auto, "augment": n_augment, "human": n_human}

    # Impact level label
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

    # Narrative: reference the five-element model
    title = summary.get("title", "this occupation")

    # Find the top and bottom scoring elements
    sorted_elems = sorted(element_scores.items(), key=lambda x: x[1], reverse=True)
    elem_labels = {
        "efficiency": "Efficiency & Time Savings",
        "quality": "Quality & Accuracy",
        "cost": "Cost Impact",
        "revenue": "Revenue & Innovation",
        "service": "Service & Satisfaction",
    }
    top_elem = sorted_elems[0]
    low_elem = sorted_elems[-1]

    role_summary = (
        f"AI is projected to have a <strong>{impact_level.lower()}</strong> impact on "
        f"<strong>{html.escape(title)}</strong>. Across {n_total} tasks scored on five business "
        f"dimensions, the highest potential is in <strong>{elem_labels[top_elem[0]]}</strong> "
        f"(avg {top_elem[1]}/9), while <strong>{elem_labels[low_elem[0]]}</strong> "
        f"(avg {low_elem[1]}/9) shows the least AI-driven change. "
    )

    if overall_score >= 65:
        role_summary += (
            "Organizations should proactively build AI adoption roadmaps, starting with "
            "high-scoring efficiency and cost tasks to capture quick wins, then expanding "
            "into quality and innovation use cases."
        )
    elif overall_score >= 40:
        role_summary += (
            "This role offers substantial AI augmentation opportunities. Focus initial investments "
            "on the highest-scoring elements to demonstrate ROI before broader transformation."
        )
    else:
        role_summary += (
            "AI will primarily serve as a support tool for this role. Target selective "
            "automation of routine sub-tasks while preserving the human expertise that "
            "defines this occupation's core value."
        )

    # Strategic outlook
    if overall_score >= 65:
        outlook = (
            "High-impact role transformation expected within 2-4 years. The strongest business "
            f"case is in {elem_labels[top_elem[0]]} where AI can deliver immediate measurable "
            "gains. Organizations should establish AI centers of excellence and begin phased "
            "deployment. Professionals should invest in AI orchestration and validation skills."
        )
    elif overall_score >= 40:
        outlook = (
            "Steady evolution over 3-5 years as AI augmentation tools mature. Early adopters "
            "will gain significant productivity advantages. The core role persists but with "
            "higher expectations for output volume and analytical depth. Focus upskilling on "
            "AI literacy and the tools most relevant to this occupation's highest-scoring elements."
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
        "element_scores": element_scores,
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
                       knowledge: list, abilities: list, ai_impact: dict,
                       industries: list = None, education: list = None,
                       job_zone: dict = None, technologies: list = None,
                       bls_by_state: list = None, bls_by_industry: list = None,
                       bls_national: int = 0) -> str:
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
    industries_json = json.dumps(industries or [])
    education_json = json.dumps(education or [])
    job_zone_json = json.dumps(job_zone or {})
    technologies_json = json.dumps((technologies or [])[:20])  # top 20 techs
    summary_json = json.dumps(summary)
    bls_state_json = json.dumps(bls_by_state or [])
    bls_industry_json = json.dumps(bls_by_industry or [])
    bls_national_val = bls_national or 0
    element_scores_json = json.dumps(ai_impact.get("element_scores", {}))

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

        /* Analysis tab */
        .analysis-hero {{
            background: linear-gradient(135deg, #1B2A4A 0%, #1e3a5f 100%);
            color: white;
            border-radius: var(--radius);
            padding: 28px 32px;
            margin-bottom: var(--gap);
        }}
        .analysis-hero h2 {{ font-size: 20px; font-weight: 700; margin-bottom: 10px; }}
        .analysis-hero .desc {{ font-size: 14px; line-height: 1.8; opacity: 0.92; }}
        .analysis-hero .badges {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }}
        .bright-badge {{
            display: inline-flex; align-items: center; gap: 6px;
            background: rgba(16,185,129,0.2); border: 1px solid rgba(16,185,129,0.4);
            color: #6EE7B7; padding: 4px 12px; border-radius: 20px;
            font-size: 12px; font-weight: 600;
        }}
        .sample-titles {{ font-size: 12px; opacity: 0.7; margin-top: 10px; }}
        .info-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: var(--gap);
            margin-bottom: var(--gap);
        }}
        .info-card {{
            background: var(--bg-card);
            border-radius: var(--radius);
            padding: 22px 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        }}
        .info-card h4 {{ font-size: 13px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.8px; margin-bottom: 10px; }}
        .info-card .info-value {{ font-size: 15px; font-weight: 600; margin-bottom: 4px; }}
        .info-card .info-detail {{ font-size: 13px; color: var(--text-secondary); line-height: 1.6; }}
        .tech-list {{ display: flex; flex-wrap: wrap; gap: 8px; }}
        .tech-badge {{
            display: inline-flex; align-items: center; gap: 6px;
            background: #EFF6FF; border: 1px solid #BFDBFE;
            color: #1E40AF; padding: 5px 12px; border-radius: 8px;
            font-size: 12px; font-weight: 500;
        }}
        .tech-badge.hot {{ background: #FEF3C7; border-color: #FCD34D; color: #92400E; }}
        .tech-pct {{ font-size: 10px; opacity: 0.7; }}
        .trend-card {{
            background: var(--bg-card);
            border-radius: var(--radius);
            padding: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
            margin-bottom: var(--gap);
        }}
        .trend-card h3 {{ font-size: 15px; font-weight: 600; margin-bottom: 8px; }}

        /* Narrative sections */
        .narrative-section {{
            background: var(--bg-card);
            border-radius: var(--radius);
            padding: 24px 28px;
            margin-bottom: var(--gap);
            border: 1px solid #e5e7eb;
        }}
        .narrative-section h3 {{
            font-size: 16px;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 14px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .narrative-section h3 .n-icon {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 28px;
            height: 28px;
            border-radius: 8px;
            font-size: 14px;
        }}
        .narrative-section p {{
            font-size: 14px;
            color: var(--text-secondary);
            line-height: 1.9;
            margin-bottom: 12px;
        }}
        .narrative-section p:last-child {{ margin-bottom: 0; }}
        .narrative-section strong {{ color: var(--text-primary); }}
        .narrative-section .highlight {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 6px;
            font-weight: 600;
            font-size: 13px;
        }}
        .highlight-blue {{ background: #EFF6FF; color: #1D4ED8; }}
        .highlight-green {{ background: #ECFDF5; color: #065F46; }}
        .highlight-amber {{ background: #FFFBEB; color: #92400E; }}
        .highlight-purple {{ background: #F5F3FF; color: #5B21B6; }}
        .highlight-rose {{ background: #FFF1F2; color: #9F1239; }}
        .insight-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 14px;
            margin-top: 16px;
        }}
        .insight-item {{
            background: var(--bg-primary);
            border-radius: 8px;
            padding: 16px;
            border: 1px solid #f3f4f6;
        }}
        .insight-item .i-label {{
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            color: var(--text-secondary);
            margin-bottom: 4px;
        }}
        .insight-item .i-value {{
            font-size: 18px;
            font-weight: 700;
            color: var(--text-primary);
        }}
        .insight-item .i-note {{
            font-size: 12px;
            color: var(--text-secondary);
            margin-top: 4px;
            line-height: 1.5;
        }}
        .skills-narrative-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 14px;
            margin-top: 14px;
        }}
        @media (max-width: 700px) {{ .skills-narrative-grid {{ grid-template-columns: 1fr; }} }}
        .skill-group {{
            background: var(--bg-primary);
            border-radius: 8px;
            padding: 16px;
            border: 1px solid #f3f4f6;
        }}
        .skill-group h4 {{
            font-size: 13px;
            font-weight: 700;
            color: var(--accent);
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .skill-group ul {{
            list-style: none;
            padding: 0;
        }}
        .skill-group ul li {{
            font-size: 13px;
            color: var(--text-secondary);
            padding: 3px 0;
            display: flex;
            justify-content: space-between;
        }}
        .skill-group ul li span.score {{
            font-weight: 600;
            color: var(--text-primary);
        }}
        .trend-kpis {{ display: flex; gap: 32px; flex-wrap: wrap; margin-bottom: 16px; }}
        .trend-kpi {{ text-align: center; }}
        .trend-kpi .val {{ font-size: 28px; font-weight: 800; color: var(--accent); }}
        .trend-kpi .lbl {{ font-size: 11px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; }}

        /* Jobs tab */
        .jobs-summary {{
            background: linear-gradient(135deg, #065F46 0%, #047857 100%);
            color: white; border-radius: var(--radius);
            padding: 24px 32px; margin-bottom: var(--gap);
            display: flex; gap: 32px; flex-wrap: wrap; align-items: center;
        }}
        .jobs-summary .jobs-metric {{ text-align: center; }}
        .jobs-summary .jobs-metric .val {{ font-size: 30px; font-weight: 800; }}
        .jobs-summary .jobs-metric .lbl {{ font-size: 11px; opacity: 0.8; text-transform: uppercase; letter-spacing: 0.5px; }}
        .growth-badge {{
            display: inline-block; padding: 2px 10px; border-radius: 12px;
            font-size: 11px; font-weight: 600;
        }}
        .growth-much-faster {{ background: #D1FAE5; color: #065F46; }}
        .growth-faster {{ background: #DBEAFE; color: #1E40AF; }}
        .growth-average {{ background: #F3F4F6; color: #4B5563; }}
        .growth-slower {{ background: #FEF3C7; color: #92400E; }}
        .growth-decline {{ background: #FEE2E2; color: #991B1B; }}

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

        .ai-filter-row {{
            display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap;
        }}
        .table-responsive {{
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }}
        .table-responsive table {{
            min-width: 700px;
        }}

        /* Score cell heat colors */
        .score-cell {{
            display: inline-block; width: 32px; height: 32px; line-height: 32px;
            text-align: center; border-radius: 6px; font-weight: 700; font-size: 13px;
        }}

        /* Footer */
        .footer {{
            text-align: center;
            padding: 20px;
            font-size: 12px;
            color: var(--text-secondary);
        }}

        /* ── Responsive Design ────────────────────────────────── */

        /* Tablet (≤ 1024px) */
        @media (max-width: 1024px) {{
            .container {{ padding: 12px; }}
            .header {{ padding: 20px 24px; }}
            .header h1 {{ font-size: 20px; }}
            .chart-row {{ grid-template-columns: 1fr; }}
            .kpi-row {{ grid-template-columns: repeat(3, 1fr); }}
            .detail-grid {{ grid-template-columns: repeat(2, 1fr); }}
            .agent-grid {{ grid-template-columns: repeat(2, 1fr); }}
            .skills-narrative-grid {{ grid-template-columns: repeat(2, 1fr); }}
        }}

        /* Mobile (≤ 768px) */
        @media (max-width: 768px) {{
            :root {{ --gap: 10px; }}
            .container {{ padding: 8px; }}
            .header {{ padding: 16px; border-radius: 8px; }}
            .header h1 {{ font-size: 18px; }}
            .header .desc {{ font-size: 13px; }}
            .kpi-row {{ grid-template-columns: repeat(2, 1fr); gap: 8px; }}
            .kpi-card {{ padding: 14px 16px; }}
            .kpi-card .kpi-value {{ font-size: 28px; }}
            .kpi-card .kpi-label {{ font-size: 11px; }}

            /* Scrollable tab bar */
            .tab-bar {{
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
                flex-wrap: nowrap;
                gap: 4px;
                padding-bottom: 4px;
            }}
            .tab {{
                padding: 8px 12px;
                font-size: 12px;
                white-space: nowrap;
                flex-shrink: 0;
            }}

            .chart-card, .table-card, .outlook-card {{ padding: 14px; border-radius: 8px; }}
            .chart-card h3, .table-card h3 {{ font-size: 14px; }}
            .detail-grid, .agent-grid, .ai-skill-grid {{ grid-template-columns: 1fr; }}
            .skills-narrative-grid {{ grid-template-columns: 1fr; }}
            .ai-score-ring {{ flex-direction: column; align-items: flex-start; gap: 16px; }}
            .ai-metrics {{ flex-direction: row; gap: 24px; }}

            .chart-row {{ gap: 10px; }}
            .jobs-summary {{ padding: 16px; gap: 16px; }}
            .jobs-summary .jobs-metric .val {{ font-size: 22px; }}

            .analysis-hero {{ padding: 18px; }}
            .analysis-hero h2 {{ font-size: 20px; }}

            .insight-grid {{ grid-template-columns: repeat(2, 1fr); gap: 10px; }}
            .insight-item {{ padding: 12px; }}
            .insight-item .i-value {{ font-size: 22px; }}

            .narrative-section {{ padding: 18px; }}
            .narrative-section p {{ font-size: 13px; }}

            .trend-kpis {{ gap: 16px; }}
            .trend-kpi .val {{ font-size: 22px; }}

            table {{ font-size: 12px; }}
            th, td {{ padding: 8px 10px; }}
        }}

        /* Small mobile (≤ 480px) */
        @media (max-width: 480px) {{
            .kpi-row {{ grid-template-columns: repeat(2, 1fr); gap: 6px; }}
            .kpi-card {{ padding: 10px 12px; }}
            .kpi-card .kpi-value {{ font-size: 22px; }}
            .kpi-card .kpi-label {{ font-size: 10px; }}
            .header h1 {{ font-size: 16px; }}
            .header .code {{ font-size: 11px; }}
            .header .desc {{ font-size: 12px; line-height: 1.5; }}
            .tab {{ padding: 6px 10px; font-size: 11px; }}
            .insight-grid {{ grid-template-columns: 1fr 1fr; }}
            .back-btn {{ font-size: 12px; padding: 6px 12px; }}
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

        <!-- KPI Row: Five-Element AI Impact Scores -->
        <div class="kpi-row">
            <div class="kpi-card" style="border-left-color:#3B82F6">
                <div class="kpi-label">Efficiency &amp; Time</div>
                <div class="kpi-value" id="kpi-efficiency" style="color:#3B82F6">0</div>
                <div class="kpi-sub">out of 9</div>
            </div>
            <div class="kpi-card" style="border-left-color:#10B981">
                <div class="kpi-label">Quality &amp; Accuracy</div>
                <div class="kpi-value" id="kpi-quality" style="color:#10B981">0</div>
                <div class="kpi-sub">out of 9</div>
            </div>
            <div class="kpi-card" style="border-left-color:#F59E0B">
                <div class="kpi-label">Cost Impact</div>
                <div class="kpi-value" id="kpi-cost" style="color:#F59E0B">0</div>
                <div class="kpi-sub">out of 9</div>
            </div>
            <div class="kpi-card" style="border-left-color:#8B5CF6">
                <div class="kpi-label">Revenue &amp; Innovation</div>
                <div class="kpi-value" id="kpi-revenue" style="color:#8B5CF6">0</div>
                <div class="kpi-sub">out of 9</div>
            </div>
            <div class="kpi-card" style="border-left-color:#EC4899">
                <div class="kpi-label">Service &amp; Satisfaction</div>
                <div class="kpi-value" id="kpi-service" style="color:#EC4899">0</div>
                <div class="kpi-sub">out of 9</div>
            </div>
            <div class="kpi-card ai-impact" style="border-left-color:#EF4444">
                <div class="kpi-label">Overall AI Impact</div>
                <div class="kpi-value" id="kpi-ai-score" style="color:var(--ai-color)">0</div>
                <div class="kpi-sub" id="kpi-ai-level"></div>
            </div>
        </div>

        <!-- Tab Navigation -->
        <div class="tab-bar">
            <button class="tab active" onclick="switchTab('analysis', this)">Analysis</button>
            <button class="tab" onclick="switchTab('jobs', this)">Jobs</button>
            <button class="tab" onclick="switchTab('overview', this)">Overview</button>
            <button class="tab ai-tab" onclick="switchTab('ai-impact', this)">AI Impact</button>
            <button class="tab" onclick="switchTab('tasks', this)">Tasks</button>
            <button class="tab" onclick="switchTab('skills', this)">Skills</button>
            <button class="tab" onclick="switchTab('knowledge', this)">Knowledge</button>
            <button class="tab" onclick="switchTab('abilities', this)">Abilities</button>
        </div>

        <!-- Analysis Tab -->
        <div class="tab-content active" id="tab-analysis">
            <!-- Occupation Hero -->
            <div class="analysis-hero">
                <h2>{title}</h2>
                <div class="desc">{description}</div>
                <div class="badges" id="analysis-badges"></div>
                <div class="sample-titles" id="analysis-sample-titles"></div>
            </div>

            <!-- Workforce Overview Narrative -->
            <div class="narrative-section" id="narrative-overview">
                <h3><span class="n-icon" style="background:#EFF6FF;color:#3B82F6;">&#9432;</span> Workforce Overview</h3>
                <div id="narrative-overview-content"></div>
            </div>

            <!-- Key Facts -->
            <div class="info-grid" id="analysis-info-grid">
                <div class="info-card" id="card-education">
                    <h4>Education</h4>
                    <div id="education-content"></div>
                </div>
                <div class="info-card" id="card-jobzone">
                    <h4>Preparation Level</h4>
                    <div id="jobzone-content"></div>
                </div>
                <div class="info-card" id="card-outlook">
                    <h4>Employment Outlook</h4>
                    <div id="outlook-content"></div>
                </div>
            </div>

            <!-- Skills & Competencies Narrative -->
            <div class="narrative-section" id="narrative-skills">
                <h3><span class="n-icon" style="background:#ECFDF5;color:#10B981;">&#9881;</span> Skills &amp; Competencies Profile</h3>
                <div id="narrative-skills-content"></div>
            </div>

            <!-- Technologies -->
            <div class="table-card">
                <h3>In-Demand Technologies &amp; Tools</h3>
                <div class="tech-list" id="tech-list"></div>
            </div>

            <!-- Industry Landscape Narrative -->
            <div class="narrative-section" id="narrative-industries">
                <h3><span class="n-icon" style="background:#F5F3FF;color:#8B5CF6;">&#9878;</span> Industry Landscape</h3>
                <div id="narrative-industries-content"></div>
            </div>

            <!-- Industries -->
            <div class="chart-row">
                <div class="chart-card">
                    <h3><span class="dot" style="background:#3B82F6"></span> Top Industries by Employment Share</h3>
                    <canvas id="chart-analysis-industries"></canvas>
                </div>
                <div class="chart-card">
                    <h3><span class="dot" style="background:#10B981"></span> Employment Trends</h3>
                    <div class="trend-kpis" id="trend-kpis"></div>
                    <canvas id="chart-analysis-trends"></canvas>
                </div>
            </div>

            <!-- Career Pathway & Business Value Narrative -->
            <div class="narrative-section" id="narrative-career">
                <h3><span class="n-icon" style="background:#FFFBEB;color:#D97706;">&#9734;</span> Career Pathway &amp; Business Value</h3>
                <div id="narrative-career-content"></div>
            </div>

            <!-- AI Impact Summary for Analysis tab -->
            <div class="trend-card" style="border-left: 4px solid var(--ai-color);">
                <h3>AI Impact on This Occupation</h3>
                <p style="font-size:14px; color:var(--text-secondary); line-height:1.8;" id="analysis-ai-summary"></p>
                <div style="margin-top:14px; display:flex; gap:16px; flex-wrap:wrap;">
                    <div style="text-align:center;">
                        <div style="font-size:28px; font-weight:800; color:var(--ai-color);" id="analysis-ai-score">0</div>
                        <div style="font-size:11px; color:var(--text-secondary); text-transform:uppercase;">Impact Score</div>
                    </div>
                    <div style="text-align:center;">
                        <div style="font-size:22px; font-weight:700; color:#3B82F6;" id="analysis-ai-efficiency">0</div>
                        <div style="font-size:10px; color:var(--text-secondary); text-transform:uppercase;">Efficiency</div>
                    </div>
                    <div style="text-align:center;">
                        <div style="font-size:22px; font-weight:700; color:#10B981;" id="analysis-ai-quality">0</div>
                        <div style="font-size:10px; color:var(--text-secondary); text-transform:uppercase;">Quality</div>
                    </div>
                    <div style="text-align:center;">
                        <div style="font-size:22px; font-weight:700; color:#F59E0B;" id="analysis-ai-cost">0</div>
                        <div style="font-size:10px; color:var(--text-secondary); text-transform:uppercase;">Cost</div>
                    </div>
                    <div style="text-align:center;">
                        <div style="font-size:22px; font-weight:700; color:#8B5CF6;" id="analysis-ai-revenue">0</div>
                        <div style="font-size:10px; color:var(--text-secondary); text-transform:uppercase;">Revenue</div>
                    </div>
                    <div style="text-align:center;">
                        <div style="font-size:22px; font-weight:700; color:#EC4899;" id="analysis-ai-service">0</div>
                        <div style="font-size:10px; color:var(--text-secondary); text-transform:uppercase;">Service</div>
                    </div>
                </div>
            </div>

            <!-- AI Strategy Narrative -->
            <div class="narrative-section" id="narrative-ai-strategy">
                <h3><span class="n-icon" style="background:#FFF1F2;color:#E11D48;">&#9881;</span> AI Strategy &amp; Workforce Implications</h3>
                <div id="narrative-ai-strategy-content"></div>
            </div>
        </div>

        <!-- Jobs Tab -->
        <div class="tab-content" id="tab-jobs">
            <!-- Jobs Summary Banner -->
            <div class="jobs-summary" id="jobs-summary"></div>

            <!-- Charts Row 1: State data -->
            <div class="chart-row">
                <div class="chart-card" style="flex:2">
                    <h3><span class="dot" style="background:#3B82F6"></span> Employment by State — Top 20</h3>
                    <div style="height:480px"><canvas id="chart-jobs-state-bar"></canvas></div>
                </div>
                <div class="chart-card" style="flex:1">
                    <h3><span class="dot" style="background:#10B981"></span> State Employment Share</h3>
                    <canvas id="chart-jobs-state-doughnut"></canvas>
                </div>
            </div>

            <!-- Charts Row 2: Industry data -->
            <div class="chart-row">
                <div class="chart-card" style="flex:2">
                    <h3><span class="dot" style="background:#8B5CF6"></span> Employment by Industry — Top 15</h3>
                    <div style="height:440px"><canvas id="chart-jobs-industry-bar"></canvas></div>
                </div>
                <div class="chart-card" style="flex:1">
                    <h3><span class="dot" style="background:#F59E0B"></span> Industry Employment Share</h3>
                    <canvas id="chart-jobs-industry-doughnut"></canvas>
                </div>
            </div>

            <!-- State Employment Table -->
            <div class="table-card">
                <h3>Employment by State — Full Data</h3>
                <div id="jobs-state-table"></div>
            </div>

            <!-- Industry Employment Table -->
            <div class="table-card">
                <h3>Employment by Industry — Full Data</h3>
                <div id="jobs-industry-table"></div>
            </div>

            <div style="text-align:center; color:var(--text-secondary); font-size:11px; margin-top:8px; padding:8px;">
                Source: U.S. Bureau of Labor Statistics, Occupational Employment and Wage Statistics (OEWS)
            </div>
        </div>

        <!-- Overview Tab -->
        <div class="tab-content" id="tab-overview">
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
                <h2>AI Business Impact Assessment</h2>
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

            <!-- Five-Element Radar + Bar Charts -->
            <div class="chart-row">
                <div class="chart-card">
                    <h3><span class="dot" style="background:var(--ai-color)"></span> Five-Element Impact Profile</h3>
                    <canvas id="chart-ai-radar"></canvas>
                </div>
                <div class="chart-card">
                    <h3><span class="dot" style="background:var(--ai-color)"></span> Element Scores (0-9)</h3>
                    <canvas id="chart-ai-elements-bar"></canvas>
                </div>
            </div>

            <!-- AI Distribution + Task Classification Charts -->
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

            <!-- Per-Task Five-Element Scoring Table -->
            <div class="table-card">
                <div class="section-label">Task-Level Impact Analysis</div>
                <input type="text" class="search-box" placeholder="Search tasks..." oninput="filterAITasks(this.value)">
                <div class="ai-filter-row">
                    <button class="badge" style="cursor:pointer; padding:4px 12px;" onclick="filterAIClass('all')">All</button>
                    <button class="badge badge-automate" style="cursor:pointer; padding:4px 12px;" onclick="filterAIClass('automate')">High Impact</button>
                    <button class="badge badge-augment" style="cursor:pointer; padding:4px 12px;" onclick="filterAIClass('augment')">Moderate</button>
                    <button class="badge badge-human" style="cursor:pointer; padding:4px 12px;" onclick="filterAIClass('human')">Low Impact</button>
                </div>
                <div class="table-responsive" id="ai-tasks-table"></div>
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
    const INDUSTRIES = {industries_json};
    const EDUCATION = {education_json};
    const JOB_ZONE = {job_zone_json};
    const TECHNOLOGIES = {technologies_json};
    const SUMMARY = {summary_json};
    const BLS_BY_STATE = {bls_state_json};
    const BLS_BY_INDUSTRY = {bls_industry_json};
    const BLS_NATIONAL = {bls_national_val};
    const ELEMENT_SCORES = {element_scores_json};

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

    // ── Analysis Tab ─────────────────────────────────────────────────
    (function() {{
        // Bright outlook badges
        const badgesEl = document.getElementById('analysis-badges');
        if (SUMMARY.is_bright_outlook && SUMMARY.bright_outlook) {{
            badgesEl.innerHTML = SUMMARY.bright_outlook.map(b =>
                '<span class="bright-badge">&#9733; ' + b.title + '</span>'
            ).join('');
        }}
        // Sample titles
        const samplesEl = document.getElementById('analysis-sample-titles');
        if (SUMMARY.sample_titles && SUMMARY.sample_titles.length > 0) {{
            samplesEl.textContent = 'Also known as: ' + SUMMARY.sample_titles.slice(0, 6).join(', ');
        }}

        // Education
        const eduEl = document.getElementById('education-content');
        if (EDUCATION.length > 0) {{
            eduEl.innerHTML = EDUCATION.filter(e => e.percentage_of_respondents > 0)
                .sort((a,b) => b.percentage_of_respondents - a.percentage_of_respondents)
                .map(e => '<div class="info-value">' + e.title + ' <span style="color:var(--accent);font-size:13px;">(' + e.percentage_of_respondents + '%)</span></div>')
                .join('');
        }} else {{
            eduEl.innerHTML = '<div class="info-detail">No education data available</div>';
        }}

        // Job zone
        const jzEl = document.getElementById('jobzone-content');
        if (JOB_ZONE.title) {{
            jzEl.innerHTML =
                '<div class="info-value">' + JOB_ZONE.title + '</div>' +
                '<div class="info-detail" style="margin-top:6px">' + (JOB_ZONE.education || '') + '</div>';
        }} else {{
            jzEl.innerHTML = '<div class="info-detail">No job zone data available</div>';
        }}

        // Outlook summary
        const outEl = document.getElementById('outlook-content');
        if (INDUSTRIES.length > 0) {{
            const growth = INDUSTRIES[0].projected_growth || 'N/A';
            const openings = INDUSTRIES[0].projected_openings || 0;
            const numIndustries = INDUSTRIES.length;
            outEl.innerHTML =
                '<div class="info-value">Growth: ' + growth + '</div>' +
                '<div class="info-value">5-Year Openings: ' + openings.toLocaleString() + '</div>' +
                '<div class="info-detail" style="margin-top:6px">Present in ' + numIndustries + ' industr' + (numIndustries === 1 ? 'y' : 'ies') + '</div>' +
                (SUMMARY.is_bright_outlook ? '<div style="margin-top:8px;"><span class="bright-badge" style="background:rgba(16,185,129,0.15);color:#059669;border-color:#A7F3D0;">Bright Outlook</span></div>' : '');
        }} else {{
            outEl.innerHTML = '<div class="info-detail">No outlook data available</div>';
        }}

        // Technologies
        const techEl = document.getElementById('tech-list');
        if (TECHNOLOGIES.length > 0) {{
            techEl.innerHTML = TECHNOLOGIES.slice(0, 15).map(t =>
                '<span class="tech-badge' + (t.hot_technology ? ' hot' : '') + '">' +
                t.title + ' <span class="tech-pct">' + (t.percentage > 0 ? t.percentage + '%' : '') + '</span>' +
                '</span>'
            ).join('');
        }} else {{
            techEl.innerHTML = '<span style="color:var(--text-secondary);font-size:13px;">No technology data available</span>';
        }}

        // Industries chart
        if (INDUSTRIES.length > 0) {{
            const top = INDUSTRIES.slice(0, 10);
            const ctx = document.getElementById('chart-analysis-industries').getContext('2d');
            new Chart(ctx, {{
                type: 'bar',
                data: {{
                    labels: top.map(d => d.industry.length > 35 ? d.industry.substring(0,35) + '...' : d.industry),
                    datasets: [{{
                        data: top.map(d => d.percent_employed),
                        backgroundColor: '#3B82F6CC',
                        borderColor: '#3B82F6',
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
                                title: ctx => {{ const i = ctx[0].dataIndex; return top[i].industry; }},
                                label: ctx => 'Employment share: ' + ctx.parsed.x + '%'
                            }}
                        }}
                    }},
                    scales: {{
                        x: {{ beginAtZero: true, title: {{ display: true, text: '% of Workers in This Occupation', font: {{ size: 11 }} }}, grid: {{ color: '#f3f4f6' }} }},
                        y: {{ ticks: {{ font: {{ size: 11 }} }}, grid: {{ display: false }} }}
                    }}
                }}
            }});
        }}

        // Trends chart — estimated openings by industry
        if (INDUSTRIES.length > 0) {{
            const total = INDUSTRIES[0].projected_openings || 0;
            const numInd = INDUSTRIES.length;
            const kpisEl = document.getElementById('trend-kpis');
            kpisEl.innerHTML =
                '<div class="trend-kpi"><div class="val">' + total.toLocaleString() + '</div><div class="lbl">Total 5-Year Openings</div></div>' +
                '<div class="trend-kpi"><div class="val">' + numInd + '</div><div class="lbl">Industries Hiring</div></div>' +
                '<div class="trend-kpi"><div class="val">' + (INDUSTRIES[0].projected_growth || 'N/A') + '</div><div class="lbl">Growth Rate</div></div>';

            const topTrend = INDUSTRIES.filter(d => d.estimated_industry_openings > 0).slice(0, 8);
            if (topTrend.length > 0) {{
                const ctx2 = document.getElementById('chart-analysis-trends').getContext('2d');
                new Chart(ctx2, {{
                    type: 'bar',
                    data: {{
                        labels: topTrend.map(d => d.industry.length > 30 ? d.industry.substring(0,30) + '...' : d.industry),
                        datasets: [{{
                            data: topTrend.map(d => d.estimated_industry_openings),
                            backgroundColor: '#10B981CC',
                            borderColor: '#10B981',
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
                                    title: ctx => {{ const i = ctx[0].dataIndex; return topTrend[i].industry; }},
                                    label: ctx => 'Estimated openings: ' + ctx.parsed.x.toLocaleString()
                                }}
                            }}
                        }},
                        scales: {{
                            x: {{ beginAtZero: true, title: {{ display: true, text: 'Estimated 5-Year Openings', font: {{ size: 11 }} }}, grid: {{ color: '#f3f4f6' }} }},
                            y: {{ ticks: {{ font: {{ size: 11 }} }}, grid: {{ display: false }} }}
                        }}
                    }}
                }});
            }}
        }}

        // AI Impact summary on Analysis tab
        document.getElementById('analysis-ai-summary').innerHTML = AI_IMPACT.role_summary;
        document.getElementById('analysis-ai-score').textContent = AI_IMPACT.overall_score;
        document.getElementById('analysis-ai-efficiency').textContent = (ELEMENT_SCORES.efficiency || 0).toFixed(1);
        document.getElementById('analysis-ai-quality').textContent = (ELEMENT_SCORES.quality || 0).toFixed(1);
        document.getElementById('analysis-ai-cost').textContent = (ELEMENT_SCORES.cost || 0).toFixed(1);
        document.getElementById('analysis-ai-revenue').textContent = (ELEMENT_SCORES.revenue || 0).toFixed(1);
        document.getElementById('analysis-ai-service').textContent = (ELEMENT_SCORES.service || 0).toFixed(1);

        // ─── Narrative: Workforce Overview ────────────────────────────
        (function() {{
            const el = document.getElementById('narrative-overview-content');
            const title = SUMMARY.title || 'This occupation';
            const totalTasks = TASKS.length;
            const highTasks = TASKS.filter(t => t.score >= 70).length;
            const nat = BLS_NATIONAL || BLS_BY_STATE.reduce((s,d) => s + d.employment, 0);
            const numStates = BLS_BY_STATE.length;
            const numInd = BLS_BY_INDUSTRY.length || INDUSTRIES.length;
            const topEdu = EDUCATION.filter(e => e.percentage_of_respondents > 0).sort((a,b) => b.percentage_of_respondents - a.percentage_of_respondents)[0];
            const jzTitle = JOB_ZONE.title || '';

            let html = '<p>';
            html += '<strong>' + title + '</strong> professionals ';
            if (nat > 0) {{
                html += 'represent a workforce of approximately <span class="highlight highlight-blue">' + nat.toLocaleString() + ' workers nationally</span>';
                if (numStates > 0) html += ', employed across <strong>' + numStates + ' states</strong>';
                html += '. ';
            }}
            html += 'The role encompasses <strong>' + totalTasks + ' distinct tasks</strong>';
            if (highTasks > 0) html += ', of which <strong>' + highTasks + '</strong> are rated as high-importance activities that define the core of daily work';
            html += '.</p>';

            html += '<p>';
            if (topEdu) {{
                html += 'The most common educational pathway is a <strong>' + topEdu.title + '</strong> (held by ' + topEdu.percentage_of_respondents + '% of workers). ';
            }}
            if (jzTitle) {{
                html += 'O*NET classifies this as <span class="highlight highlight-purple">' + jzTitle + '</span>';
                if (JOB_ZONE.experience) html += ', typically requiring ' + JOB_ZONE.experience.toLowerCase();
                html += '. ';
            }}
            if (numInd > 0) {{
                html += 'These professionals are hired across <strong>' + numInd + ' distinct industries</strong>, reflecting broad demand across the economy.';
            }}
            html += '</p>';

            // Key metrics grid
            html += '<div class="insight-grid">';
            if (nat > 0) {{
                html += '<div class="insight-item"><div class="i-label">National Workforce</div><div class="i-value">' + nat.toLocaleString() + '</div><div class="i-note">BLS OEWS estimate</div></div>';
            }}
            html += '<div class="insight-item"><div class="i-label">Core Tasks</div><div class="i-value">' + totalTasks + '</div><div class="i-note">' + highTasks + ' high-importance</div></div>';
            html += '<div class="insight-item"><div class="i-label">Skills Required</div><div class="i-value">' + SKILLS.length + '</div><div class="i-note">' + SKILLS.filter(s => s.score >= 60).length + ' critical skills</div></div>';
            html += '<div class="insight-item"><div class="i-label">Knowledge Areas</div><div class="i-value">' + KNOWLEDGE.length + '</div><div class="i-note">' + KNOWLEDGE.filter(k => k.score >= 60).length + ' essential domains</div></div>';
            html += '</div>';

            el.innerHTML = html;
        }})();

        // ─── Narrative: Skills & Competencies ─────────────────────────
        (function() {{
            const el = document.getElementById('narrative-skills-content');
            const title = SUMMARY.title || 'This occupation';
            const topSkills = SKILLS.slice(0, 5);
            const topKnowledge = KNOWLEDGE.slice(0, 5);
            const topAbilities = ABILITIES.slice(0, 5);
            const criticalSkills = SKILLS.filter(s => s.score >= 70);
            const foundationalKnowledge = KNOWLEDGE.filter(k => k.score >= 60);

            let html = '<p>Success as a <strong>' + title + '</strong> demands a blend of technical expertise and professional competencies. ';
            if (topSkills.length > 0) {{
                html += 'The most critical skill is <strong>' + topSkills[0].name + '</strong> (importance: ' + topSkills[0].score + '/100)';
                if (topSkills.length > 2) {{
                    html += ', followed by <strong>' + topSkills[1].name + '</strong> and <strong>' + topSkills[2].name + '</strong>';
                }}
                html += '. ';
            }}
            if (criticalSkills.length > 0) {{
                html += 'Overall, <span class="highlight highlight-green">' + criticalSkills.length + ' skills are rated as critical</span> (importance ≥ 70), signaling a role that requires well-rounded capabilities.</p>';
            }} else {{
                html += '</p>';
            }}

            html += '<p>';
            if (foundationalKnowledge.length > 0) {{
                html += 'From a knowledge perspective, <strong>' + foundationalKnowledge[0].name + '</strong>';
                if (foundationalKnowledge.length > 1) html += ' and <strong>' + foundationalKnowledge[1].name + '</strong>';
                html += ' form the intellectual foundation. ';
            }}
            if (topAbilities.length > 0) {{
                html += 'Key cognitive abilities include <strong>' + topAbilities[0].name + '</strong>';
                if (topAbilities.length > 1) html += ' and <strong>' + topAbilities[1].name + '</strong>';
                html += ', which are essential for effective performance.';
            }}
            html += '</p>';

            // Skill breakdown grid
            html += '<div class="skills-narrative-grid">';
            html += '<div class="skill-group"><h4>Top Skills</h4><ul>';
            topSkills.forEach(s => {{ html += '<li>' + s.name + ' <span class="score">' + s.score + '</span></li>'; }});
            html += '</ul></div>';
            html += '<div class="skill-group"><h4>Top Knowledge</h4><ul>';
            topKnowledge.forEach(k => {{ html += '<li>' + k.name + ' <span class="score">' + k.score + '</span></li>'; }});
            html += '</ul></div>';
            html += '<div class="skill-group"><h4>Top Abilities</h4><ul>';
            topAbilities.forEach(a => {{ html += '<li>' + a.name + ' <span class="score">' + a.score + '</span></li>'; }});
            html += '</ul></div>';
            html += '<div class="skill-group"><h4>Key Technologies</h4><ul>';
            TECHNOLOGIES.slice(0, 5).forEach(t => {{ html += '<li>' + t.title + ' <span class="score">' + (t.percentage > 0 ? t.percentage + '%' : '—') + '</span></li>'; }});
            html += '</ul></div>';
            html += '</div>';

            el.innerHTML = html;
        }})();

        // ─── Narrative: Industry Landscape ────────────────────────────
        (function() {{
            const el = document.getElementById('narrative-industries-content');
            const title = SUMMARY.title || 'This occupation';
            const hasONET = INDUSTRIES.length > 0;
            const hasBLS = BLS_BY_INDUSTRY.length > 0;

            let html = '';
            if (hasBLS) {{
                const top3 = BLS_BY_INDUSTRY.slice(0, 3);
                const totalBLS = BLS_BY_INDUSTRY.reduce((s,d) => s + d.employment, 0);
                const top3pct = totalBLS > 0 ? ((top3.reduce((s,d) => s + d.employment, 0) / totalBLS) * 100).toFixed(0) : 0;
                const concentration = top3pct > 70 ? 'highly concentrated' : top3pct > 50 ? 'moderately concentrated' : 'broadly distributed';

                html += '<p>Bureau of Labor Statistics data shows <strong>' + title + '</strong> employment is ' + concentration + ' across industries. ';
                html += 'The top three employing industries — ';
                html += top3.map((d,i) => '<strong>' + d.industry + '</strong>' + (i < 2 && i < top3.length - 1 ? ', ' : '')).join('');
                html += ' — account for <span class="highlight highlight-purple">' + top3pct + '% of all positions</span>. ';
                html += 'In total, <strong>' + BLS_BY_INDUSTRY.length + ' industries</strong> employ workers in this occupation.</p>';

                if (BLS_BY_INDUSTRY.length > 5) {{
                    const emerging = BLS_BY_INDUSTRY.slice(3, 6);
                    html += '<p>Beyond the primary industries, notable employment also exists in ';
                    html += emerging.map(d => '<strong>' + d.industry + '</strong> (' + d.employment.toLocaleString() + ' workers)').join(', ');
                    html += '. This breadth of industry demand provides career flexibility and resilience against sector-specific downturns.</p>';
                }}
            }} else if (hasONET) {{
                const top3 = INDUSTRIES.slice(0, 3);
                html += '<p>O*NET data identifies <strong>' + INDUSTRIES.length + ' industries</strong> that employ <strong>' + title + '</strong> professionals. ';
                if (top3.length > 0) {{
                    html += 'The largest concentration is in <strong>' + top3[0].industry + '</strong> (' + top3[0].percent_employed + '% of workers)';
                    if (top3.length > 1) html += ', followed by <strong>' + top3[1].industry + '</strong> (' + top3[1].percent_employed + '%)';
                    html += '.</p>';
                }}
            }} else {{
                html += '<p>Industry distribution data is not currently available for this occupation.</p>';
            }}

            // State insight if available
            if (BLS_BY_STATE.length > 0) {{
                const topStates = BLS_BY_STATE.slice(0, 5);
                const nat = BLS_NATIONAL || BLS_BY_STATE.reduce((s,d) => s + d.employment, 0);
                const topPct = nat > 0 ? ((topStates.reduce((s,d) => s + d.employment, 0) / nat) * 100).toFixed(0) : 0;
                html += '<p><strong>Geographic concentration:</strong> The top five states — ';
                html += topStates.map(s => s.state).join(', ');
                html += ' — employ <span class="highlight highlight-blue">' + topPct + '% of the national workforce</span>. ';
                html += 'This suggests that organizations in these states face the most competitive hiring markets for this role.</p>';
            }}

            el.innerHTML = html;
        }})();

        // ─── Narrative: Career Pathway & Business Value ───────────────
        (function() {{
            const el = document.getElementById('narrative-career-content');
            const title = SUMMARY.title || 'This occupation';
            const nat = BLS_NATIONAL || BLS_BY_STATE.reduce((s,d) => s + d.employment, 0);
            const hasGrowth = INDUSTRIES.length > 0 && INDUSTRIES[0].projected_growth;
            const growth = hasGrowth ? INDUSTRIES[0].projected_growth : '';
            const openings = INDUSTRIES.length > 0 ? (INDUSTRIES[0].projected_openings || 0) : 0;
            const isBright = SUMMARY.is_bright_outlook;

            let html = '<p>';
            if (hasGrowth) {{
                const growthLower = growth.toLowerCase();
                if (growthLower.includes('faster') || growthLower.includes('much faster')) {{
                    html += 'The outlook for <strong>' + title + '</strong> is notably positive, with projected growth rated as <span class="highlight highlight-green">' + growth + '</span> than the national average. ';
                }} else if (growthLower.includes('average')) {{
                    html += '<strong>' + title + '</strong> positions are expected to grow at an <span class="highlight highlight-amber">' + growth.toLowerCase() + '</span> pace. ';
                }} else {{
                    html += 'Growth for <strong>' + title + '</strong> roles is projected as <span class="highlight highlight-amber">' + growth.toLowerCase() + '</span>. ';
                }}
            }}
            if (openings > 0) {{
                html += 'An estimated <strong>' + openings.toLocaleString() + ' job openings</strong> are projected over the next five years from both growth and replacement needs. ';
            }}
            if (isBright) {{
                html += 'O*NET designates this as a <span class="highlight highlight-green">Bright Outlook</span> occupation, indicating strong hiring prospects.';
            }}
            html += '</p>';

            // Business value narrative
            html += '<p><strong>Business impact:</strong> ';
            if (SKILLS.length > 0 && KNOWLEDGE.length > 0) {{
                const techSkills = SKILLS.filter(s => ['Programming','Computers and Electronics','Engineering and Technology','Mathematics','Systems Analysis','Technology Design','Complex Problem Solving'].some(k => s.name.includes(k) || s.name.toLowerCase().includes(k.toLowerCase())));
                const interpSkills = SKILLS.filter(s => ['Critical Thinking','Active Listening','Judgment','Decision Making','Communication','Coordination','Social Perceptiveness'].some(k => s.name.includes(k) || s.name.toLowerCase().includes(k.toLowerCase())));

                if (techSkills.length > 0 && interpSkills.length > 0) {{
                    html += 'This role combines both technical depth and interpersonal capability, making it a high-value position for organizations. ';
                    html += 'The blend of analytical skills (such as ' + techSkills.slice(0,2).map(s => s.name).join(' and ') + ') with professional competencies (including ' + interpSkills.slice(0,2).map(s => s.name).join(' and ') + ') ';
                    html += 'means these professionals directly influence operational efficiency, innovation capacity, and strategic decision-making.';
                }} else {{
                    html += 'Professionals in this role bring specialized expertise that directly contributes to organizational performance and competitive advantage.';
                }}
            }} else {{
                html += 'Professionals in this role bring specialized expertise that directly contributes to organizational performance.';
            }}
            html += '</p>';

            // Talent strategy callout
            if (nat > 100000) {{
                html += '<p><strong>Talent strategy consideration:</strong> With over ' + (Math.floor(nat / 100000) * 100000).toLocaleString() + ' professionals in the national labor market, this is a sizable but competitive talent pool. Organizations should invest in employer branding, competitive compensation, and retention strategies to attract and keep top performers.</p>';
            }} else if (nat > 10000) {{
                html += '<p><strong>Talent strategy consideration:</strong> With approximately ' + nat.toLocaleString() + ' professionals nationally, this is a specialized talent pool. Targeted recruiting, partnerships with educational institutions, and internal development pipelines are key strategies for building capacity.</p>';
            }}

            el.innerHTML = html;
        }})();

        // ─── Narrative: AI Strategy & Workforce Implications ──────────
        (function() {{
            const el = document.getElementById('narrative-ai-strategy-content');
            const title = SUMMARY.title || 'This occupation';
            const es = ELEMENT_SCORES;
            const score = AI_IMPACT.overall_score || 0;
            const agents = AI_IMPACT.agents || [];
            const autoCount = AI_IMPACT.distribution.automate || 0;
            const augCount = AI_IMPACT.distribution.augment || 0;
            const humanCount = AI_IMPACT.distribution.human || 0;
            const totalTasks = autoCount + augCount + humanCount;

            let html = '<p>';
            if (score >= 70) {{
                html += 'AI will significantly reshape the <strong>' + title + '</strong> role. With an overall impact score of <span class="highlight highlight-rose">' + score + '/100</span>, ';
                html += 'organizations should proactively develop transition plans. ';
            }} else if (score >= 40) {{
                html += 'AI presents substantial augmentation opportunities for <strong>' + title + '</strong> professionals. With a moderate impact score of <span class="highlight highlight-amber">' + score + '/100</span>, ';
                html += 'the focus should be on upskilling and tool adoption rather than role elimination. ';
            }} else {{
                html += 'AI impact on the <strong>' + title + '</strong> role is relatively limited, with a score of <span class="highlight highlight-green">' + score + '/100</span>. ';
                html += 'The human-centric nature of this work provides strong resilience against automation. ';
            }}
            html += '</p>';

            // Five-element breakdown narrative
            const elements = [
                {{name: 'Efficiency & Time Savings', val: es.efficiency || 0, color: 'blue'}},
                {{name: 'Quality & Accuracy', val: es.quality || 0, color: 'green'}},
                {{name: 'Cost Impact', val: es.cost || 0, color: 'amber'}},
                {{name: 'Revenue & Innovation', val: es.revenue || 0, color: 'purple'}},
                {{name: 'Service & Satisfaction', val: es.service || 0, color: 'rose'}}
            ];
            const sorted = [...elements].sort((a,b) => b.val - a.val);
            const top = sorted[0];
            const bottom = sorted[sorted.length - 1];

            html += '<p><strong>Five-element analysis:</strong> The strongest AI opportunity is in ';
            html += '<span class="highlight highlight-' + top.color + '">' + top.name + ' (' + top.val.toFixed(1) + '/9)</span>';
            if (sorted.length > 1 && sorted[1].val >= 3) {{
                html += ', followed by <strong>' + sorted[1].name + '</strong> (' + sorted[1].val.toFixed(1) + '/9)';
            }}
            html += '. The lowest impact area is <strong>' + bottom.name + '</strong> (' + bottom.val.toFixed(1) + '/9)';
            html += ', suggesting this dimension requires more human judgment and oversight.</p>';

            if (totalTasks > 0) {{
                const autoPct = ((autoCount / totalTasks) * 100).toFixed(0);
                const augPct = ((augCount / totalTasks) * 100).toFixed(0);
                const humanPct = ((humanCount / totalTasks) * 100).toFixed(0);
                html += '<p>Across <strong>' + totalTasks + ' tasks</strong>: ';
                html += '<span class="highlight highlight-rose">' + autoPct + '% score as high-impact</span>, ';
                html += '<span class="highlight highlight-amber">' + augPct + '% are moderate-impact</span>, and ';
                html += '<span class="highlight highlight-green">' + humanPct + '% are low-impact</span>. ';
                if (parseInt(augPct) > parseInt(autoPct)) {{
                    html += 'AI will primarily serve as a force multiplier, enabling professionals to handle greater volume and complexity rather than replacing them.';
                }} else if (parseInt(autoPct) > 40) {{
                    html += 'The high proportion of high-impact tasks signals that role responsibilities will shift toward higher-value activities as routine work is automated.';
                }}
                html += '</p>';
            }}

            if (agents.length > 0) {{
                html += '<p><strong>Recommended AI investments:</strong> ';
                html += 'Based on task analysis, ' + agents.length + ' AI agent ' + (agents.length === 1 ? 'type is' : 'types are') + ' relevant for this role. ';
                html += 'The highest-impact deployments include ';
                html += agents.slice(0, 3).map(a => '<strong>' + a.name + '</strong>').join(', ');
                html += '. These tools can deliver measurable productivity gains while allowing workers to focus on the judgment-intensive and relationship-driven aspects of their work.</p>';
            }}

            el.innerHTML = html;
        }})();

    }})();

    // ── Jobs Tab (BLS OEWS Data) ───────────────────────────────────────
    (function() {{
        const hasBLS = BLS_BY_STATE.length > 0 || BLS_BY_INDUSTRY.length > 0;
        if (!hasBLS) {{
            document.getElementById('jobs-summary').innerHTML = '<div style="text-align:center;width:100%"><div style="font-size:16px;font-weight:600;">No BLS employment data available</div><div style="font-size:13px;opacity:0.8;margin-top:4px;">Bureau of Labor Statistics data was not found for this occupation.</div></div>';
            return;
        }}

        const totalNational = BLS_NATIONAL || BLS_BY_STATE.reduce((s,d) => s + d.employment, 0);
        const numStates = BLS_BY_STATE.length;
        const numIndustries = BLS_BY_INDUSTRY.length;
        const topState = BLS_BY_STATE.length > 0 ? BLS_BY_STATE[0] : null;
        const topIndustry = BLS_BY_INDUSTRY.length > 0 ? BLS_BY_INDUSTRY[0] : null;

        // Summary banner
        let bannerHTML = '<div class="jobs-metric"><div class="val">' + totalNational.toLocaleString() + '</div><div class="lbl">National Employment</div></div>';
        bannerHTML += '<div class="jobs-metric"><div class="val">' + numStates + '</div><div class="lbl">States with Jobs</div></div>';
        bannerHTML += '<div class="jobs-metric"><div class="val">' + numIndustries + '</div><div class="lbl">Industries Hiring</div></div>';
        if (topState) {{
            bannerHTML += '<div class="jobs-metric"><div class="val">' + topState.state + '</div><div class="lbl">Top State (' + topState.employment.toLocaleString() + ')</div></div>';
        }}
        document.getElementById('jobs-summary').innerHTML = bannerHTML;

        const palette = ['#3B82F6','#10B981','#F59E0B','#EC4899','#8B5CF6','#EF4444','#06B6D4','#84CC16','#14B8A6','#F97316','#A855F7','#0EA5E9','#22C55E','#E11D48','#7C3AED','#D946EF','#FB923C','#2DD4BF','#4ADE80','#9CA3AF'];

        // ─── State Bar Chart (Top 20) ───
        if (BLS_BY_STATE.length > 0) {{
            const topStates = BLS_BY_STATE.slice(0, 20);
            const ctx1 = document.getElementById('chart-jobs-state-bar').getContext('2d');
            new Chart(ctx1, {{
                type: 'bar',
                data: {{
                    labels: topStates.map(d => d.state),
                    datasets: [{{
                        label: 'Employment',
                        data: topStates.map(d => d.employment),
                        backgroundColor: '#3B82F6CC',
                        borderColor: '#3B82F6',
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
                                label: ctx => ctx.parsed.x.toLocaleString() + ' employed'
                            }}
                        }}
                    }},
                    scales: {{
                        x: {{ beginAtZero: true, title: {{ display: true, text: 'Employment Count', font: {{ size: 11 }} }}, grid: {{ color: '#f3f4f6' }},
                            ticks: {{ callback: v => v >= 1000 ? (v/1000).toFixed(0) + 'K' : v }} }},
                        y: {{ ticks: {{ font: {{ size: 11 }} }}, grid: {{ display: false }} }}
                    }}
                }}
            }});

            // State doughnut (top 8 + other)
            const top8s = BLS_BY_STATE.slice(0, 8);
            const otherEmp = BLS_BY_STATE.slice(8).reduce((s,d) => s + d.employment, 0);
            const sLabels = top8s.map(d => d.state);
            const sData = top8s.map(d => d.employment);
            if (otherEmp > 0) {{ sLabels.push('Other States'); sData.push(otherEmp); }}

            const ctx2 = document.getElementById('chart-jobs-state-doughnut').getContext('2d');
            new Chart(ctx2, {{
                type: 'doughnut',
                data: {{
                    labels: sLabels,
                    datasets: [{{ data: sData, backgroundColor: palette.slice(0, sLabels.length).map(c => c + 'CC'), borderColor: '#fff', borderWidth: 2 }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '40%',
                    plugins: {{
                        legend: {{ position: 'bottom', labels: {{ usePointStyle: true, padding: 10, font: {{ size: 10 }} }} }},
                        tooltip: {{
                            callbacks: {{
                                label: ctx => {{
                                    const pct = totalNational > 0 ? ((ctx.parsed / totalNational) * 100).toFixed(1) : '0';
                                    return ctx.label + ': ' + ctx.parsed.toLocaleString() + ' (' + pct + '%)';
                                }}
                            }}
                        }}
                    }}
                }}
            }});
        }}

        // ─── Industry Bar Chart (Top 15) ───
        if (BLS_BY_INDUSTRY.length > 0) {{
            const topInd = BLS_BY_INDUSTRY.slice(0, 15);
            const ctx3 = document.getElementById('chart-jobs-industry-bar').getContext('2d');
            new Chart(ctx3, {{
                type: 'bar',
                data: {{
                    labels: topInd.map(d => d.industry.length > 40 ? d.industry.substring(0,40) + '...' : d.industry),
                    datasets: [{{
                        label: 'Employment',
                        data: topInd.map(d => d.employment),
                        backgroundColor: '#8B5CF6CC',
                        borderColor: '#8B5CF6',
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
                                title: ctx => {{ const i = ctx[0].dataIndex; return topInd[i].industry; }},
                                label: ctx => ctx.parsed.x.toLocaleString() + ' employed'
                            }}
                        }}
                    }},
                    scales: {{
                        x: {{ beginAtZero: true, title: {{ display: true, text: 'Employment Count', font: {{ size: 11 }} }}, grid: {{ color: '#f3f4f6' }},
                            ticks: {{ callback: v => v >= 1000 ? (v/1000).toFixed(0) + 'K' : v }} }},
                        y: {{ ticks: {{ font: {{ size: 11 }} }}, grid: {{ display: false }} }}
                    }}
                }}
            }});

            // Industry doughnut (top 8 + other)
            const top8i = BLS_BY_INDUSTRY.slice(0, 8);
            const otherInd = BLS_BY_INDUSTRY.slice(8).reduce((s,d) => s + d.employment, 0);
            const iLabels = top8i.map(d => d.industry.length > 30 ? d.industry.substring(0,30) + '...' : d.industry);
            const iData = top8i.map(d => d.employment);
            if (otherInd > 0) {{ iLabels.push('Other Industries'); iData.push(otherInd); }}

            const indTotal = BLS_BY_INDUSTRY.reduce((s,d) => s + d.employment, 0);
            const ctx4 = document.getElementById('chart-jobs-industry-doughnut').getContext('2d');
            new Chart(ctx4, {{
                type: 'doughnut',
                data: {{
                    labels: iLabels,
                    datasets: [{{ data: iData, backgroundColor: palette.slice(0, iLabels.length).map(c => c + 'CC'), borderColor: '#fff', borderWidth: 2 }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '40%',
                    plugins: {{
                        legend: {{ position: 'bottom', labels: {{ usePointStyle: true, padding: 10, font: {{ size: 10 }} }} }},
                        tooltip: {{
                            callbacks: {{
                                label: ctx => {{
                                    const pct = indTotal > 0 ? ((ctx.parsed / indTotal) * 100).toFixed(1) : '0';
                                    return ctx.label + ': ' + ctx.parsed.toLocaleString() + ' (' + pct + '%)';
                                }}
                            }}
                        }}
                    }}
                }}
            }});
        }}

        // ─── State Table ───
        if (BLS_BY_STATE.length > 0) {{
            let html = '<table><thead><tr>';
            html += '<th style="width:40px">#</th>';
            html += '<th>State</th>';
            html += '<th style="width:160px">Employment</th>';
            html += '<th style="width:130px">% of National</th>';
            html += '</tr></thead><tbody>';

            BLS_BY_STATE.forEach((d, i) => {{
                const pct = totalNational > 0 ? ((d.employment / totalNational) * 100).toFixed(1) : '0';
                const barW = totalNational > 0 ? ((d.employment / BLS_BY_STATE[0].employment) * 100).toFixed(0) : 0;
                html += '<tr>';
                html += '<td style="color:var(--text-secondary);font-size:12px;">' + (i+1) + '</td>';
                html += '<td><strong>' + d.state + '</strong></td>';
                html += '<td style="text-align:right; font-weight:600;">' + d.employment.toLocaleString() + '</td>';
                html += '<td><div class="score-row"><div class="score-bar" style="flex:1"><div class="score-fill" style="width:' + barW + '%;background:#3B82F6"></div></div><span>' + pct + '%</span></div></td>';
                html += '</tr>';
            }});
            html += '</tbody></table>';
            document.getElementById('jobs-state-table').innerHTML = html;
        }}

        // ─── Industry Table ───
        if (BLS_BY_INDUSTRY.length > 0) {{
            let html2 = '<table><thead><tr>';
            html2 += '<th style="width:40px">#</th>';
            html2 += '<th>Industry</th>';
            html2 += '<th style="width:160px">Employment</th>';
            html2 += '<th style="width:130px">% of Total</th>';
            html2 += '</tr></thead><tbody>';

            const indSum = BLS_BY_INDUSTRY.reduce((s,d) => s + d.employment, 0);
            BLS_BY_INDUSTRY.forEach((d, i) => {{
                const pct = indSum > 0 ? ((d.employment / indSum) * 100).toFixed(1) : '0';
                const barW = indSum > 0 ? ((d.employment / BLS_BY_INDUSTRY[0].employment) * 100).toFixed(0) : 0;
                html2 += '<tr>';
                html2 += '<td style="color:var(--text-secondary);font-size:12px;">' + (i+1) + '</td>';
                html2 += '<td><strong>' + d.industry + '</strong></td>';
                html2 += '<td style="text-align:right; font-weight:600;">' + d.employment.toLocaleString() + '</td>';
                html2 += '<td><div class="score-row"><div class="score-bar" style="flex:1"><div class="score-fill" style="width:' + barW + '%;background:#8B5CF6"></div></div><span>' + pct + '%</span></div></td>';
                html2 += '</tr>';
            }});
            html2 += '</tbody></table>';
            document.getElementById('jobs-industry-table').innerHTML = html2;
        }}
    }})();

    // ── Tab Switching ──────────────────────────────────────────────────
    function switchTab(name, btn) {{
        document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
        document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
        document.getElementById('tab-' + name).classList.add('active');
        btn.classList.add('active');
    }}

    // ── KPIs (Five-Element Scores) ────────────────────────────────────
    document.getElementById('kpi-efficiency').textContent = (ELEMENT_SCORES.efficiency || 0).toFixed(1);
    document.getElementById('kpi-quality').textContent = (ELEMENT_SCORES.quality || 0).toFixed(1);
    document.getElementById('kpi-cost').textContent = (ELEMENT_SCORES.cost || 0).toFixed(1);
    document.getElementById('kpi-revenue').textContent = (ELEMENT_SCORES.revenue || 0).toFixed(1);
    document.getElementById('kpi-service').textContent = (ELEMENT_SCORES.service || 0).toFixed(1);
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

    // ── Five-Element Radar Chart ──────────────────────────────────────
    (function() {{
        const es = ELEMENT_SCORES;
        const ctx = document.getElementById('chart-ai-radar').getContext('2d');
        new Chart(ctx, {{
            type: 'radar',
            data: {{
                labels: ['Efficiency & Time', 'Quality & Accuracy', 'Cost Impact', 'Revenue & Innovation', 'Service & Satisfaction'],
                datasets: [{{
                    label: 'Element Score',
                    data: [es.efficiency || 0, es.quality || 0, es.cost || 0, es.revenue || 0, es.service || 0],
                    backgroundColor: 'rgba(236,72,153,0.2)',
                    borderColor: '#EC4899',
                    borderWidth: 2,
                    pointBackgroundColor: '#EC4899',
                    pointRadius: 5,
                    pointHoverRadius: 7,
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    r: {{
                        beginAtZero: true,
                        max: 9,
                        ticks: {{ stepSize: 3, font: {{ size: 11 }}, backdropColor: 'transparent' }},
                        grid: {{ color: 'rgba(0,0,0,0.08)' }},
                        pointLabels: {{ font: {{ size: 11, weight: 600 }}, color: '#374151' }},
                        angleLines: {{ color: 'rgba(0,0,0,0.08)' }}
                    }}
                }},
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        callbacks: {{
                            label: ctx => ctx.dataset.label + ': ' + ctx.parsed.r.toFixed(1) + ' / 9'
                        }}
                    }}
                }}
            }}
        }});
    }})();

    // ── Five-Element Bar Chart ────────────────────────────────────────
    (function() {{
        const es = ELEMENT_SCORES;
        const labels = ['Efficiency', 'Quality', 'Cost', 'Revenue', 'Service'];
        const values = [es.efficiency || 0, es.quality || 0, es.cost || 0, es.revenue || 0, es.service || 0];
        const colors = ['#3B82F6', '#10B981', '#F59E0B', '#8B5CF6', '#EC4899'];

        const ctx = document.getElementById('chart-ai-elements-bar').getContext('2d');
        new Chart(ctx, {{
            type: 'bar',
            data: {{
                labels: labels,
                datasets: [{{
                    data: values,
                    backgroundColor: colors.map(c => c + 'CC'),
                    borderColor: colors,
                    borderWidth: 1,
                    borderRadius: 6,
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        callbacks: {{
                            label: ctx => ctx.label + ': ' + ctx.parsed.y.toFixed(1) + ' / 9'
                        }}
                    }}
                }},
                scales: {{
                    y: {{ beginAtZero: true, max: 9, ticks: {{ stepSize: 3 }}, grid: {{ color: '#f3f4f6' }} }},
                    x: {{ grid: {{ display: false }} }}
                }}
            }}
        }});
    }})();

    // AI Distribution doughnut
    (function() {{
        const d = AI_IMPACT.distribution;
        const ctx = document.getElementById('chart-ai-distribution').getContext('2d');
        new Chart(ctx, {{
            type: 'doughnut',
            data: {{
                labels: ['High Impact (Automate)', 'Moderate (Augment)', 'Low Impact (Human)'],
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

    // AI tasks horizontal bar — avg score by task
    (function() {{
        const ta = AI_IMPACT.task_analysis;
        const truncate = (s, n) => s.length > n ? s.substring(0, n) + '...' : s;
        const sorted = [...ta].sort((a,b) => b.avg_score - a.avg_score).slice(0, 15);

        const ctx = document.getElementById('chart-ai-tasks-bar').getContext('2d');
        new Chart(ctx, {{
            type: 'bar',
            data: {{
                labels: sorted.map(t => truncate(t.statement, 55)),
                datasets: [{{
                    data: sorted.map(t => t.avg_score),
                    backgroundColor: sorted.map(t =>
                        t.avg_score >= 5 ? COLORS.automate + 'CC' :
                        t.avg_score >= 2.5 ? COLORS.augment + 'CC' : COLORS.human + 'CC'
                    ),
                    borderColor: sorted.map(t =>
                        t.avg_score >= 5 ? COLORS.automate :
                        t.avg_score >= 2.5 ? COLORS.augment : COLORS.human
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
                                return sorted[idx].statement;
                            }},
                            label: ctx => {{
                                const idx = ctx.dataIndex;
                                const t = sorted[idx];
                                return ['Avg Score: ' + t.avg_score.toFixed(1) + ' / 9',
                                        'Efficiency: ' + t.efficiency + '  Quality: ' + t.quality,
                                        'Cost: ' + t.cost + '  Revenue: ' + t.revenue + '  Service: ' + t.service];
                            }}
                        }}
                    }}
                }},
                scales: {{
                    x: {{ beginAtZero: true, max: 9, grid: {{ color: '#f3f4f6' }} }},
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

    // ── Task-Level Five-Element Scoring Table ─────────────────────────
    let aiTaskFilter = '';
    let aiClassFilter = 'all';
    let aiSortCol = 'avg_score';
    let aiSortDir = 'desc';

    function scoreColor(v) {{
        if (v >= 7) return '#DC2626';
        if (v >= 5) return '#F59E0B';
        if (v >= 3) return '#3B82F6';
        if (v >= 1) return '#10B981';
        return '#9CA3AF';
    }}

    function scoreBg(v) {{
        if (v >= 7) return 'rgba(220,38,38,0.12)';
        if (v >= 5) return 'rgba(245,158,11,0.12)';
        if (v >= 3) return 'rgba(59,130,246,0.12)';
        if (v >= 1) return 'rgba(16,185,129,0.12)';
        return 'rgba(156,163,175,0.08)';
    }}

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
        html += '<th onclick="sortAITasks(\\'statement\\')" style="min-width:200px">Task' + arrow('statement') + '</th>';
        html += '<th onclick="sortAITasks(\\'efficiency\\')" class="score-cell">Efficiency' + arrow('efficiency') + '</th>';
        html += '<th onclick="sortAITasks(\\'quality\\')" class="score-cell">Quality' + arrow('quality') + '</th>';
        html += '<th onclick="sortAITasks(\\'cost\\')" class="score-cell">Cost' + arrow('cost') + '</th>';
        html += '<th onclick="sortAITasks(\\'revenue\\')" class="score-cell">Revenue' + arrow('revenue') + '</th>';
        html += '<th onclick="sortAITasks(\\'service\\')" class="score-cell">Service' + arrow('service') + '</th>';
        html += '<th onclick="sortAITasks(\\'avg_score\\')" class="score-cell" style="font-weight:700">Avg' + arrow('avg_score') + '</th>';
        html += '</tr></thead><tbody>';

        data.forEach(t => {{
            html += '<tr>';
            html += '<td>' + t.statement + '</td>';
            ['efficiency','quality','cost','revenue','service'].forEach(key => {{
                const v = t[key] || 0;
                html += '<td class="score-cell" style="background:' + scoreBg(v) + ';color:' + scoreColor(v) + ';font-weight:700;text-align:center">' + v + '</td>';
            }});
            const avg = t.avg_score || 0;
            html += '<td class="score-cell" style="background:' + scoreBg(avg) + ';color:' + scoreColor(avg) + ';font-weight:800;text-align:center;font-size:14px">' + avg.toFixed(1) + '</td>';
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

    # New: Education, Job Zone, Technologies
    education = get_education_requirements(code, api_key)
    print(f"  ✓ Education ({len(education)} levels)")

    job_zone = get_job_zone(code, api_key)
    print(f"  ✓ Job Zone: {job_zone.get('title', 'N/A')}")

    technologies = get_hot_technologies(code, api_key)
    print(f"  ✓ Technologies ({len(technologies)})")

    # New: Industry data (this scans all industries — may take a moment)
    print("  ⏳ Scanning industries...")
    industries = get_occupation_industries(code, api_key)
    print(f"  ✓ Industries ({len(industries)} found)")

    # BLS Employment Data
    bls_key = os.environ.get("BLS_API_KEY", "")
    print("  ⏳ Fetching BLS employment data...")
    bls_national = get_bls_national_employment(code, bls_key)
    print(f"  ✓ National employment: {bls_national:,}")

    bls_by_state = get_bls_employment_by_state(code, bls_key)
    print(f"  ✓ State employment ({len(bls_by_state)} states)")

    bls_by_industry = get_bls_employment_by_industry(code, bls_key)
    print(f"  ✓ Industry employment ({len(bls_by_industry)} industries)")

    # AI Impact Analysis
    print("  ⚡ Analyzing AI impact...")
    ai_impact = analyze_ai_impact(summary, tasks, skills, knowledge, abilities)
    print(f"  ✓ AI Impact: {ai_impact['impact_level']} (score: {ai_impact['overall_score']})")
    print(f"    Tasks: {ai_impact['distribution']['automate']} automatable, "
          f"{ai_impact['distribution']['augment']} augmentable, "
          f"{ai_impact['distribution']['human']} human-essential")
    print(f"    Recommended agents: {len(ai_impact['agents'])}")

    # Generate dashboard
    dashboard_html = generate_dashboard(
        summary, tasks, skills, knowledge, abilities, ai_impact,
        industries=industries, education=education,
        job_zone=job_zone, technologies=technologies,
        bls_by_state=bls_by_state, bls_by_industry=bls_by_industry,
        bls_national=bls_national
    )

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
