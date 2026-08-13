import os
import requests
import json
import re

try:
    from . import llm_client
except ImportError:
    import llm_client
import sys
sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web_app"))
from secure_paths import SETTINGS_FILE as PRIVATE_SETTINGS_FILE

# Configuration for LLM (Matching amazon_video_maker.py)
LLM_SERVICE = "longcat"
LONGCAT_API_KEYS = []
LONGCAT_ENDPOINT = "https://api.longcat.chat/v1/chat/completions"
LONGCAT_MODEL = "longcat-flash"

GEMINI_API_KEYS = []
GEMINI_MODEL = "gemini-1.5-flash"

OPENROUTER_API_KEYS = []
OPENROUTER_MODEL = "google/gemini-2.0-flash-exp:free"

OPENAI_API_KEYS = []
OPENAI_MODEL = "gpt-4o-mini"

DEEPSEEK_API_KEYS = []
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_ENDPOINT = "https://api.deepseek.com/chat/completions"

VERTEX_PROJECT_ID = ""
VERTEX_LOCATION = "us-central1"
VERTEX_SERVICE_ACCOUNT_JSON = ""
VERTEX_LLM_MODEL = "gemini-2.5-flash"

LLM_FALLBACK_ENABLED = False
LLM_CHAIN_RAW = ""
LLM_TIMEOUT_SECONDS = 120


def _positive_int(value, fallback):
    """Settings arrive as strings from the form; a blank or junk
    timeout must not crash the render, it must fall back."""
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback

USE_YEAR = True
USE_BEST = True
YEAR = "2026"

SHOW_AFFILIATE = True
SHOW_DESCRIPTION = True
SHOW_KEYWORDS = True
SHOW_HASHTAGS = True
SHOW_DISCLAIMER = True
WEBSITE_URL = ""
CHANNEL_URL = ""
YOUTUBE_API_KEY = ""

def load_settings():
    global LONGCAT_API_KEYS, LONGCAT_ENDPOINT, LONGCAT_MODEL, USE_YEAR, USE_BEST, YEAR
    global LLM_SERVICE, GEMINI_API_KEYS, GEMINI_MODEL, OPENROUTER_API_KEYS, OPENROUTER_MODEL, OPENAI_API_KEYS, OPENAI_MODEL, DEEPSEEK_API_KEYS, DEEPSEEK_MODEL, DEEPSEEK_ENDPOINT
    global VERTEX_PROJECT_ID, VERTEX_LOCATION, VERTEX_SERVICE_ACCOUNT_JSON, VERTEX_LLM_MODEL
    global LLM_FALLBACK_ENABLED, LLM_CHAIN_RAW, LLM_TIMEOUT_SECONDS
    global SHOW_AFFILIATE, SHOW_DESCRIPTION, SHOW_KEYWORDS, SHOW_HASHTAGS, SHOW_DISCLAIMER, WEBSITE_URL, CHANNEL_URL, YOUTUBE_API_KEY
    
    settings_path = str(PRIVATE_SETTINGS_FILE)
    if os.path.exists(settings_path):
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                s = json.load(f)
                LLM_SERVICE = s.get('llm_service', LLM_SERVICE)
                
                lk = s.get('longcat_api_key', "")
                if lk:
                    LONGCAT_API_KEYS = [k.strip() for k in lk.split('\n') if k.strip()]
                LONGCAT_ENDPOINT = s.get('longcat_endpoint', LONGCAT_ENDPOINT)
                LONGCAT_MODEL = s.get('longcat_model', LONGCAT_MODEL)
                
                gk = s.get('gemini_api_key', "")
                if gk:
                    GEMINI_API_KEYS = [k.strip() for k in gk.split('\n') if k.strip()]
                GEMINI_MODEL = s.get('gemini_model', GEMINI_MODEL)
                
                ork = s.get('openrouter_api_key', "")
                if ork:
                    OPENROUTER_API_KEYS = [k.strip() for k in ork.split('\n') if k.strip()]
                OPENROUTER_MODEL = s.get('openrouter_model', OPENROUTER_MODEL)
                
                oai_k = s.get('openai_api_key', "")
                if oai_k:
                    OPENAI_API_KEYS = [k.strip() for k in oai_k.split('\n') if k.strip()]
                OPENAI_MODEL = s.get('openai_model', OPENAI_MODEL)

                dk = s.get('deepseek_api_key', "")
                if dk:
                    DEEPSEEK_API_KEYS = [k.strip() for k in dk.split('\n') if k.strip()]
                DEEPSEEK_MODEL = s.get('deepseek_model', DEEPSEEK_MODEL)
                DEEPSEEK_ENDPOINT = s.get('deepseek_endpoint', DEEPSEEK_ENDPOINT)

                VERTEX_PROJECT_ID = s.get('vertex_project_id', VERTEX_PROJECT_ID)
                VERTEX_LOCATION = s.get('vertex_location', VERTEX_LOCATION) or "us-central1"
                VERTEX_SERVICE_ACCOUNT_JSON = s.get('vertex_service_account_private_key', VERTEX_SERVICE_ACCOUNT_JSON)
                VERTEX_LLM_MODEL = s.get('vertex_llm_model', VERTEX_LLM_MODEL)

                LLM_FALLBACK_ENABLED = s.get('llm_fallback_enabled', LLM_FALLBACK_ENABLED)
                LLM_CHAIN_RAW = s.get('llm_chain', LLM_CHAIN_RAW)
                LLM_TIMEOUT_SECONDS = _positive_int(s.get('llm_timeout_seconds'), LLM_TIMEOUT_SECONDS)

                USE_YEAR = s.get('use_year', USE_YEAR)
                USE_BEST = s.get('use_best', USE_BEST)
                YEAR = str(s.get('year', YEAR))
                
                # Toggles
                SHOW_AFFILIATE = s.get('show_affiliate', True)
                SHOW_DESCRIPTION = s.get('show_description', True)
                SHOW_KEYWORDS = s.get('show_keywords', True)
                SHOW_HASHTAGS = s.get('show_hashtags', True)
                SHOW_DISCLAIMER = s.get('show_disclaimer', True)
                
                WEBSITE_URL = s.get('website_url', "")
                CHANNEL_URL = s.get('channel_url', "")
                YOUTUBE_API_KEY = s.get('youtube_api_key', "")
        except Exception as e:
            print(f"Error loading settings: {e}")

load_settings()

def get_related_videos(channel_url, keyword):
    """Fetch up to 5 related videos from a YouTube channel using YouTube Data
    API v3. Both parameters are honored directly (this used to silently
    ignore them both and read the module-level CHANNEL_URL/keyword-less
    globals instead, which made it work only by coincidence -- callers could
    never actually inject a different channel or bias the search)."""
    if not channel_url or not YOUTUBE_API_KEY:
        return []

    # Extract handle/channel ID
    handle = ""
    if "/@" in channel_url:
        handle = channel_url.split("/@")[-1].split("/")[0].split("?")[0]
    elif "/channel/" in channel_url:
        handle = channel_url.split("/channel/")[-1].split("/")[0].split("?")[0]

    if not handle:
        return []

    try:
        channel_id = ""
        # 1. Resolve handle to channelId if needed
        if "/channel/" in channel_url:
            channel_id = handle
        else:
            # Search for the channel by handle
            id_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={handle}&type=channel&key={YOUTUBE_API_KEY}"
            id_res = requests.get(id_url, timeout=10).json()
            if 'items' in id_res and len(id_res['items']) > 0:
                channel_id = id_res['items'][0]['id']['channelId']

        if not channel_id:
            return []

        # 2. Fetch up to 5 most recent videos, biased toward this keyword so
        # unrelated uploads from the channel don't crowd out relevant ones.
        v_url = (
            "https://www.googleapis.com/youtube/v3/search?part=snippet"
            f"&channelId={channel_id}&maxResults=5&order=relevance&type=video"
            f"&q={requests.utils.quote(str(keyword or ''))}&key={YOUTUBE_API_KEY}"
        )
        v_res = requests.get(v_url, timeout=10).json()

        related = []
        if 'items' in v_res:
            for item in v_res['items'][:5]:
                if 'snippet' in item and 'id' in item and isinstance(item['id'], dict) and 'videoId' in item['id']:
                    v_title = item['snippet']['title']
                    v_id = item['id']['videoId']
                    related.append({
                        "title": v_title,
                        "url": f"https://www.youtube.com/watch?v={v_id}"
                    })
        return related[:5]

    except Exception as e:
        print(f"Error fetching related videos: {e}")
        return []

_LLM_PROVIDERS = ("gemini", "vertex_gemini", "openrouter", "openai", "deepseek", "longcat")


def _provider_config(provider):
    if provider == "gemini":
        return GEMINI_API_KEYS, GEMINI_MODEL, None
    if provider == "vertex_gemini":
        # See the matching comment in amazon_video_maker._provider_config:
        # silence is correct when Vertex was never configured at all.
        if not VERTEX_SERVICE_ACCOUNT_JSON and not VERTEX_PROJECT_ID:
            return [], VERTEX_LLM_MODEL, None
        try:
            import vertex_auth
            token = vertex_auth.get_access_token(VERTEX_SERVICE_ACCOUNT_JSON)
            url = vertex_auth.generate_content_url(VERTEX_PROJECT_ID, VERTEX_LOCATION, VERTEX_LLM_MODEL)
            return [token], VERTEX_LLM_MODEL, url
        except Exception as e:
            print(f"[WARN] Vertex AI auth failed, provider unavailable: {e}")
            return [], VERTEX_LLM_MODEL, None
    if provider == "openrouter":
        return OPENROUTER_API_KEYS, OPENROUTER_MODEL, None
    if provider == "openai":
        return OPENAI_API_KEYS, OPENAI_MODEL, None
    if provider == "deepseek":
        return DEEPSEEK_API_KEYS, DEEPSEEK_MODEL, DEEPSEEK_ENDPOINT or "https://api.deepseek.com/chat/completions"
    if provider == "longcat":
        return LONGCAT_API_KEYS, LONGCAT_MODEL, LONGCAT_ENDPOINT
    return [], "", None


def _build_llm_chain():
    """Thin wrapper over the shared llm_client.build_chain. This file used to
    carry its own copy of the ordering logic, which is exactly how it ended
    up not knowing "vertex_gemini" existed and silently routing every
    metadata call to longcat instead of the user's actual choice."""
    return llm_client.build_chain(
        LLM_SERVICE,
        _provider_config,
        fallback_enabled=LLM_FALLBACK_ENABLED,
        chain_raw=LLM_CHAIN_RAW,
        order=_LLM_PROVIDERS,
    )


def call_llm(prompt):
    chain = _build_llm_chain()
    try:
        text, provider_used = llm_client.call_chain(prompt, chain, timeout=LLM_TIMEOUT_SECONDS)
        if provider_used != LLM_SERVICE:
            print(f"[LLM] Primary provider '{LLM_SERVICE}' failed; succeeded via fallback provider '{provider_used}'.")
        return text
    except llm_client.LLMCallError as e:
        print(f"[LLM][FATAL] All configured providers/keys failed: {e}")
        return ""


def build_title_variants(keyword, products, primary_title, year=""):
    clean_keyword = re.sub(r'[<>]', '', str(keyword)).strip().title()
    count = len(products)
    if count == 1:
        candidates = [
            primary_title,
            f"{clean_keyword} Review: Pros, Cons & Verdict",
            f"{clean_keyword}: Is It Worth It?",
        ]
    else:
        year_suffix = f" {year}" if year else ""
        candidates = [
            primary_title,
            f"{clean_keyword} Buying Guide{year_suffix}: Top {count} Picks",
            f"Top {count} {clean_keyword}: What to Know Before Buying",
        ]
    variants = []
    for candidate in candidates:
        title = re.sub(r"\s+", " ", str(candidate)).strip()[:100].rstrip()
        if title and title.lower() not in {item.lower() for item in variants}:
            variants.append(title)
    while len(variants) < 3:
        variants.append(f"{clean_keyword} Buying Guide {len(variants) + 1}"[:100])
    return variants[:3]

def generate_youtube_metadata(keyword, products, partner_tag=""):
    """Generates YouTube Title, Description, and Tags with strict toggle respect."""
    load_settings()
    
    # 1. SEO Title
    prompt = f"Generate one catchy alternative SEO keyword for '{keyword}'. No year. Output ONLY keyword."
    alt_keyword = call_llm(prompt).strip('\"').strip().replace("`", "")
    for y in ["2024", "2025", "2026"]: alt_keyword = alt_keyword.replace(y, "").strip()
    if not alt_keyword: alt_keyword = f"Best {keyword.title()}"
    
    best_str = " Best" if USE_BEST else ""
    year_str = f" {YEAR}" if USE_YEAR else ""
    if len(products) == 1:
        title = keyword.title()
    else:
        title = f"Top {len(products)}{best_str} {keyword.title()}{year_str} | {alt_keyword.title()}"
    
    title = re.sub(r'[<>]', '', title)[:100].strip()
    
    description_parts = []
    
    # Order: Title, Affiliate, Website, Timestamps, Global Desc, Related, Keywords, Hashtags, Disclaimer
    description_parts.append(f"{title}\n")
    
    if SHOW_AFFILIATE:
        for i, p in enumerate(products):
            asin = p.get('asin', '')
            p_title = p.get('title', 'Product')
            link = f"https://www.amazon.com/dp/{asin}?tag={partner_tag}" if partner_tag else f"https://www.amazon.com/dp/{asin}"
            description_parts.append(f"✅ {p.get('rank', i+1)}. {p_title}\n👉 {link}\n")
    
    if WEBSITE_URL:
        site_url = WEBSITE_URL if WEBSITE_URL.startswith("http") else f"https://{WEBSITE_URL}"
        description_parts.append(f"\n🌐 Visit our website for more details: {site_url}\n")
        
    description_parts.append("\n[TIMESTAMPS_HERE]\n")
    
    if SHOW_DESCRIPTION:
        verified_product_context = "\n".join(
            f"- {p.get('title', 'Product')}: "
            + "; ".join(str(feature) for feature in (p.get('features') or [])[:8])
            for p in products
        )
        if len(products) == 1:
            d_prompt = (
                f"Write a concise, evidence-based YouTube description for '{keyword}' "
                "using ONLY the supplied product facts. Clearly present strengths, "
                "limitations, best use, and who should avoid it. Never claim personal "
                "testing. No links/tags/markdown/intro. Use American English.\n"
                f"VERIFIED PRODUCT FACTS:\n{verified_product_context}"
            )
        else:
            d_prompt = (
                f"Write a 150-word evidence-based buying-guide description for "
                f"'{keyword}' using ONLY the supplied product facts. Compare useful "
                "tradeoffs and never claim personal testing. No links/tags/markdown/"
                f"intro. Use American English.\nVERIFIED PRODUCT FACTS:\n"
                f"{verified_product_context}"
            )
        
        desc = call_llm(d_prompt).replace("*", "").replace("`", "").strip()
        if ":" in desc[:50] and ("description" in desc.lower()[:50] or "here" in desc.lower()[:50]):
            desc = desc.split(":", 1) [-1].strip()
        for y in ["2024", "2025", "2026"]: desc = desc.replace(y, YEAR)
        description_parts.append(f"\n{desc}\n")
    
    related = get_related_videos(CHANNEL_URL, keyword)
    if related:
        description_parts.append("\n📺 Related Videos You Might Like:\n")
        for v in related:
            description_parts.append(f"✅ {v['title']}\n🔗 {v['url']}\n")
            
    tags_prompt = f"Generate 15 comma-separated American English (US) tags for {keyword}. Output ONLY tags."
    raw_tags = call_llm(tags_prompt).replace("\n", ", ").replace("*", "").strip()
    t_list = [t.strip() for t in re.split(r'[,\n]', raw_tags) if t.strip()]
    final_tags = []
    seen = set()
    total_len = 0
    for t in t_list:
        t = re.sub(r'[^a-zA-Z0-9\s.-]', '', t).strip()
        if not t or t.lower() in seen: continue
        if total_len + len(t) < 450:
            final_tags.append(t)
            seen.add(t.lower())
            total_len += len(t)
    tags_str = ", ".join(final_tags)
    
    # Keep tags in YouTube's dedicated tags field. Dumping a tag list into the
    # description is both low-value for discovery and can look spammy.
        
    h_prompt = f"Generate 5 American English (US) hashtags for {keyword}. Output ONLY hashtags."
    hashtags = call_llm(h_prompt).replace("*", "").replace("\n", " ").strip()
    if SHOW_HASHTAGS:
        description_parts.append(f"\nHashtags: {hashtags}\n")
    
    if SHOW_DISCLAIMER:
        disclaimer = """
This video description contains Amazon affiliate links. As an Amazon Associate, I earn from qualifying purchases. I did not personally test or use these products. These product types, and others mentioned, are shared for informational purposes only.

Disclaimer: This video and description are for informational purposes only. Product listings are AI-generated and automatically collected. Always verify details directly with the manufacturer or seller before purchasing.
"""
        description_parts.append(disclaimer)
    
    full_desc = "".join(description_parts)
    full_desc = re.sub(r'[<>]', '', full_desc).strip()
    
    return {
        'title': title,
        'title_variants': build_title_variants(keyword, products, title, YEAR),
        'description': full_desc[:5000],
        'tags': tags_str,
        'hashtags': hashtags
    }

if __name__ == "__main__":
    print("Metadata Generator Loaded.")
