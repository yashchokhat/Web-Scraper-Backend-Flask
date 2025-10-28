from flask import Flask, jsonify, request
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import pandas as pd
from urllib.parse import quote
import re
import os  # ✅ Needed for environment variable PORT

app = Flask(__name__)
CORS(app)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
TIMEOUT = 10

# OpenWeatherMap API Key
OPENWEATHER_API_KEY = "e633e921c98cecf59a07a841de00eb42"

# Supported websites
WEBSITES = {
    "wikipedia": {
        "name": "Wikipedia",
        "url_template": "https://en.wikipedia.org/wiki/{query}",
        "selectors": {
            "title": "h1#firstHeading",
            "content": "div.mw-parser-output > p"
        },
        "icon": "📘"
    },
    "github_topics": {
        "name": "GitHub Topics",
        "url_template": "https://github.com/topics/{query}",
        "selectors": {
            "title": "h1",
            "content": "article p"
        },
        "icon": "💾"
    },
    "geeksforgeeks": {
        "name": "GeeksforGeeks",
        "url_template": "https://www.geeksforgeeks.org/{query}/",
        "selectors": {
            "title": "h1",
            "content": "article p, div.text, div.content p"
        },
        "icon": "💡"
    },
    "openweathermap": {
        "name": "OpenWeatherMap",
        "api_url": "https://api.openweathermap.org/data/2.5/weather",
        "icon": "☀️"
    }
}


# --- Wikipedia utilities ---
def find_wikipedia_match(query):
    """Find best matching article title using Wikipedia API."""
    api_url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "opensearch",
        "search": query,
        "limit": 1,
        "namespace": 0,
        "format": "json"
    }
    try:
        resp = requests.get(api_url, params=params, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        links = data[3] if len(data) > 3 else []
        if links:
            return links[0]
    except Exception:
        return None
    return None


def get_wikipedia_url_for_query(query):
    """Try direct article URL first; fallback to opensearch if needed."""
    slug = query.replace(" ", "_")
    direct_url = WEBSITES["wikipedia"]["url_template"].format(query=quote(slug))
    try:
        resp = requests.head(direct_url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code == 200:
            return resp.url
    except requests.exceptions.RequestException:
        pass
    return find_wikipedia_match(query)


# --- Scraping Helper ---
def extract_summary(url, selectors, word_limit=150):
    """Extract title and summary text from the page."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        title_elem = soup.select_one(selectors.get("title", "h1"))
        title = title_elem.get_text(strip=True) if title_elem else "No title found"

        paragraphs = soup.select(selectors.get("content", "p"))
        summary_parts = []
        for elem in paragraphs:
            text = elem.get_text(separator=" ", strip=True)
            text = re.sub(r'\s+', ' ', text)
            if text and len(text) > 20:
                summary_parts.append(text)
            if sum(len(p.split()) for p in summary_parts) > word_limit:
                break

        summary = "\n\n".join(summary_parts).strip()

        words = [
            re.sub(r'[^a-z]', '', w.lower())
            for w in summary.split()
            if w.isalpha() and len(w) > 3
        ]

        if words:
            freq_series = pd.Series(words).value_counts().head(10)
            top_words = [{"word": word, "count": int(count)} for word, count in freq_series.items()]
        else:
            top_words = []

        return {
            "success": True,
            "title": title,
            "summary": summary,
            "word_count": len(summary.split()),
            "top_words": top_words,
            "url": url
        }

    except Exception as e:
        return {"success": False, "error": f"Error: {str(e)}"}


# --- API Routes ---
@app.route('/api/websites', methods=['GET'])
def get_websites():
    """List available websites"""
    return jsonify([
        {"id": key, "name": val["name"], "icon": val.get("icon", "")}
        for key, val in WEBSITES.items()
    ])


@app.route('/api/scrape', methods=['POST'])
def scrape_content():
    data = request.get_json(force=True, silent=True) or {}
    query = (data.get('query') or "").strip()
    website_id = data.get('website_id')
    custom_url = (data.get('custom_url') or "").strip()
    word_limit = data.get('word_limit', 150)

    if not query and not custom_url:
        return jsonify({"success": False, "error": "Please provide a query or URL"}), 400

    if custom_url:
        selectors = {"title": "h1, h2", "content": "p"}
        return jsonify(extract_summary(custom_url, selectors, word_limit))

    if website_id == "wikipedia":
        page_url = get_wikipedia_url_for_query(query)
        if not page_url:
            return jsonify({"success": False, "error": f"Wikipedia page for '{query}' not found."}), 404
        result = extract_summary(page_url, WEBSITES["wikipedia"]["selectors"], word_limit)
        result["website"] = "Wikipedia"
        return jsonify(result)

    elif website_id == "github_topics":
        url = WEBSITES["github_topics"]["url_template"].format(query=quote(query.replace(" ", "-").lower()))
        result = extract_summary(url, WEBSITES["github_topics"]["selectors"], word_limit)
        result["website"] = "GitHub Topics"
        return jsonify(result)

    elif website_id == "geeksforgeeks":
        slug = quote(query.lower().replace(" ", "-"))
        url = WEBSITES["geeksforgeeks"]["url_template"].format(query=slug)
        result = extract_summary(url, WEBSITES["geeksforgeeks"]["selectors"], word_limit)
        result["website"] = "GeeksforGeeks"
        return jsonify(result)

    elif website_id == "openweathermap":
        api_url = WEBSITES["openweathermap"]["api_url"]
        params = {"q": query, "appid": OPENWEATHER_API_KEY, "units": "metric"}
        try:
            r = requests.get(api_url, params=params, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            weather = {
                "location": data.get("name"),
                "temperature": data["main"]["temp"],
                "humidity": data["main"]["humidity"],
                "condition": data["weather"][0]["description"]
            }
            return jsonify({"success": True, "weather": weather, "website": "OpenWeatherMap"})
        except Exception as e:
            return jsonify({"success": False, "error": f"OpenWeatherMap API error: {str(e)}"})

    else:
        return jsonify({"success": False, "error": "Invalid website selected"}), 400


@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "message": "Web Scraper API is running"})


# ✅ Important: Use Railway's provided port
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
