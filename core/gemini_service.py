import os
import re
import json
import time
import hashlib
import base64
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Tuple, List

CONFIG_FILE = Path(__file__).parent.parent / "config" / "gemini_config.json"
CACHE_FILE = Path(__file__).parent.parent / "config" / "gemini_cache.json"


class GeminiService:
    _cached_keys: List[str] = []
    _key_index: int = 0
    _memory_cache: dict = {}
    _cache_loaded: bool = False

    @classmethod
    def _load_cache(cls):
        if cls._cache_loaded:
            return
        if CACHE_FILE.exists():
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    cls._memory_cache = json.load(f)
            except Exception:
                cls._memory_cache = {}
        cls._cache_loaded = True

    @classmethod
    def _save_cache(cls):
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cls._memory_cache, f, indent=2)
        except Exception:
            pass

    @classmethod
    def get_api_keys(cls) -> List[str]:
        if cls._cached_keys:
            return cls._cached_keys

        # Check environment variable
        env_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if env_key:
            keys = [k.strip() for k in env_key.replace("\n", ",").split(",") if k.strip()]
            if keys:
                cls._cached_keys = keys
                return keys

        # Check persistent config file
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    raw = data.get("api_key", "")
                    if isinstance(raw, list):
                        keys = [str(k).strip() for k in raw if str(k).strip()]
                    else:
                        keys = [k.strip() for k in str(raw).replace("\n", ",").split(",") if k.strip()]
                    if keys:
                        cls._cached_keys = keys
                        os.environ["GEMINI_API_KEY"] = keys[0]
                        return keys
            except Exception:
                pass
        return []

    @classmethod
    def get_current_key(cls) -> Optional[str]:
        keys = cls.get_api_keys()
        if not keys:
            return None
        return keys[cls._key_index % len(keys)]

    @classmethod
    def _rotate_key(cls) -> Optional[str]:
        keys = cls.get_api_keys()
        if len(keys) <= 1:
            return cls.get_current_key()
        cls._key_index = (cls._key_index + 1) % len(keys)
        current = keys[cls._key_index]
        os.environ["GEMINI_API_KEY"] = current
        return current

    @staticmethod
    def _clean_key_string(raw: str) -> str:
        k = raw.strip()
        # Strip backticks, quotes
        k = k.strip("`").strip('"').strip("'")
        # Strip assignment prefix e.g. GEMINI_API_KEY=AIzaSy... or api_key: AIzaSy...
        if "=" in k:
            k = k.split("=", 1)[1].strip().strip('"').strip("'")
        elif ":" in k and not k.startswith("http"):
            k = k.split(":", 1)[1].strip().strip('"').strip("'")
        # Remove zero-width spaces, non-breaking spaces, carriage returns
        k = k.replace('\u200b', '').replace('\u00a0', '').replace('\r', '').replace('\n', '').strip()
        return k

    @classmethod
    def set_api_key(cls, raw_input: str) -> Tuple[bool, str]:
        if not raw_input or not raw_input.strip():
            return False, "API key cannot be empty."

        # Support comma-separated or newline-separated multiple keys for high-volume 10k processing
        raw_keys = [k.strip() for k in raw_input.replace("\n", ",").split(",") if k.strip()]
        keys = []
        for rk in raw_keys:
            cleaned = cls._clean_key_string(rk)
            if cleaned:
                keys.append(cleaned)

        if not keys:
            return False, "No valid API keys found in input."

        # Verify primary key with Gemini API
        valid, msg = cls.test_key(keys[0])
        if not valid:
            return False, msg

        cls._cached_keys = keys
        cls._key_index = 0
        os.environ["GEMINI_API_KEY"] = keys[0]

        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"api_key": keys if len(keys) > 1 else keys[0]}, f, indent=2)
        except Exception as e:
            return False, f"Failed to save key to disk: {e}"

        count_msg = f"{len(keys)} key(s) configured" if len(keys) > 1 else "Key verified & saved"
        return True, f"Gemini AI connected! ({count_msg})"

    @classmethod
    def remove_api_key(cls):
        cls._cached_keys = []
        cls._key_index = 0
        os.environ.pop("GEMINI_API_KEY", None)
        if CONFIG_FILE.exists():
            try:
                CONFIG_FILE.unlink()
            except Exception:
                pass

    @classmethod
    def is_enabled(cls) -> bool:
        return len(cls.get_api_keys()) > 0

    @classmethod
    def get_status(cls) -> dict:
        keys = cls.get_api_keys()
        if not keys:
            return {
                "enabled": False,
                "masked_key": None,
                "key_count": 0,
                "message": "Gemini AI is offline. Using local deterministic engine."
            }
        first = keys[0]
        masked = f"{first[:4]}...{first[-4:]}" if len(first) > 8 else "***"
        return {
            "enabled": True,
            "masked_key": masked,
            "key_count": len(keys),
            "message": f"Gemini AI Active ({len(keys)} key{'s' if len(keys)>1 else ''} loaded, rate-limit protected)."
        }

    @classmethod
    def test_key(cls, key: str) -> Tuple[bool, str]:
        cleaned_key = cls._clean_key_string(key)
        if not cleaned_key:
            return False, "Key is empty after cleaning."

        # 1. Primary Test: List models endpoint via Google REST API
        # This is the fastest, official, 0-token verification method supported by all Gemini keys
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={cleaned_key}"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "TagPilot-Remediator/1.0",
                    "Accept": "application/json"
                }
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    models = [m.get("name", "").replace("models/", "") for m in data.get("models", [])]
                    model_summary = f"{len(models)} models available" if models else "connected"
                    return True, f"Verified with Google Gemini API ({model_summary})."
        except urllib.error.HTTPError as he:
            body = he.read().decode("utf-8", errors="ignore")
            try:
                err_json = json.loads(body)
                error_info = err_json.get("error", {})
                status = error_info.get("status", "")
                message = error_info.get("message", he.reason)

                # Check for 429 RESOURCE_EXHAUSTED:
                # If rate-limited, the key is 100% genuine and authenticated!
                if he.code == 429 or status == "RESOURCE_EXHAUSTED":
                    return True, "Key is active and authenticated (temporary rate-limit detected; caching & backoff enabled)."

                # Check for 403 PERMISSION_DENIED:
                if he.code == 403 or status == "PERMISSION_DENIED":
                    return False, "Google Permission Denied: Generative Language API is not enabled for this project. Please create a key directly from Google AI Studio (https://aistudio.google.com/app/apikey)."

                # Check for 400 INVALID_ARGUMENT:
                if he.code == 400 or status == "INVALID_ARGUMENT":
                    return False, f"Invalid API Key: Google returned '{message}'. Please make sure you copied the entire key."

                return False, f"Google Error ({status}): {message}"
            except Exception:
                return False, f"HTTP Error {he.code}: {he.reason}"
        except urllib.error.URLError as ue:
            # Network / DNS / Proxy issue
            print(f"[Gemini] Network error during key verification: {ue}")
            # If it's a validly formatted AIzaSy key, allow saving with network warning
            if cleaned_key.startswith("AIzaSy") and len(cleaned_key) >= 35:
                return True, "Key saved! (Network warning: Google servers unreachable right now; will retry during tagging)."
            return False, f"Network connection failed: {ue.reason}"
        except Exception as e:
            if cleaned_key.startswith("AIzaSy") and len(cleaned_key) >= 35:
                return True, "Key saved! (Network warning: will retry during tagging)."
            return False, f"Verification failed: {str(e)}"

    @classmethod
    def generate_figure_alt_text(cls, image_bytes: bytes, context_hint: str = "") -> Optional[str]:
        """
        Calls Gemini Vision with:
        - SHA256 image caching (0 quota used for repeated figures)
        - Multi-key rotation on 429 rate limit
        - Exponential backoff retry (safe for 10,000+ pages)
        - High-quality WCAG 2.1 AA / PDF/UA-1 output
        """
        if not cls.is_enabled() or not image_bytes:
            return None

        # Check Cache first (prevents redundant API calls across 10,000 pages)
        cls._load_cache()
        img_hash = hashlib.sha256(image_bytes).hexdigest()
        cache_key = f"alt_{img_hash}"
        if cache_key in cls._memory_cache:
            return cls._memory_cache[cache_key]

        prompt = (
            "You are an expert accessibility remediator producing ISO 14289-1 (PDF/UA-1) and WCAG 2.1 AA compliant Alt Text. "
            "Examine this textbook figure/diagram/graph and describe it concisely for a screen reader user. "
            "Describe the visual curves, equations, axes, geometry, or data depicted. "
            "Return ONLY the concise description (1-2 clear sentences). "
            "Do NOT include prefixes like 'Figure shows', 'Image of', or 'Alt text:'."
        )
        if context_hint:
            clean_hint = " ".join(context_hint.split())[:180]
            prompt += f" Context: {clean_hint}"

        # Up to 3 attempts with key rotation and backoff
        for attempt in range(3):
            key = cls.get_current_key()
            if not key:
                break

            result = cls._call_gemini_vision(key, image_bytes, prompt)
            if result:
                # Save to cache
                cls._memory_cache[cache_key] = result
                cls._save_cache()
                return result

            # If failed, rotate key and wait with backoff
            cls._rotate_key()
            time.sleep(1.0 * (attempt + 1))

        return None

    @classmethod
    def _call_gemini_vision(cls, key: str, image_bytes: bytes, prompt: str) -> Optional[str]:
        # 1. Try google.genai SDK
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=key)
            part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")
            for model_name in ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3.8-flash"]:
                try:
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=[part, prompt]
                    )
                    if resp and resp.text:
                        desc = resp.text.strip().replace("\n", " ")
                        if desc.startswith('"') and desc.endswith('"'):
                            desc = desc[1:-1].strip()
                        return desc
                except Exception as me:
                    if "429" in str(me) or "RESOURCE_EXHAUSTED" in str(me):
                        # Rate limited on this model, continue to try next model in list
                        continue
                    continue
        except Exception:
            pass

        # 2. Direct REST API fallback
        try:
            b64_img = base64.b64encode(image_bytes).decode("utf-8")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={key}"
            payload = {
                "contents": [{
                    "parts": [
                        {"inline_data": {"mime_type": "image/png", "data": b64_img}},
                        {"text": prompt}
                    ]
                }]
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts and "text" in parts[0]:
                        desc = parts[0]["text"].strip().replace("\n", " ")
                        if desc.startswith('"') and desc.endswith('"'):
                            desc = desc[1:-1].strip()
                        return desc
        except Exception:
            pass

        return None

    @classmethod
    def remediate_page(cls, page, page_num: int, raw_elements: list, page_w: float, page_h: float, figures_data: list = None) -> list:
        """
        Multimodal Gemini Remediation Engine:
        Analyzes the page image alongside detected text fragments to produce
        100% unified, professional PDF/UA-1 compliant tags matching Pdf-demo:
        - Unifies broken math formulas (radicals, fractions, exponents, derivations) into single <Formula> tags with spoken alt_text.
        - Unifies wrapped sentences into single <P> tags.
        - Preserves strict heading hierarchy (H1, H2, H3).
        - Preserves atomic <Figure> elements with descriptive Alt text.
        """
        if not cls.is_enabled():
            return raw_elements

        # Cache check
        cls._load_cache()
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        page_hash = hashlib.sha256(img_bytes).hexdigest()
        cache_key = f"page_rem_v3_{page_hash}"

        cached_json = cls._memory_cache.get(cache_key)
        if cached_json:
            try:
                return json.loads(cached_json)
            except Exception:
                pass

        # Prepare compact raw elements list for prompt
        raw_list = []
        for el in raw_elements:
            txt = (el.get("content") or "").replace("\n", " ").strip()[:120]
            if el.get("tag") in ("Table", "TABLE") or el.get("type") == "table":
                row_texts = []
                for r in el.get("kids", []):
                    cells = [c.get("content") or "" for c in r.get("kids", [])]
                    row_texts.append(" | ".join(cells))
                txt = "[Table Grid: " + "; ".join(row_texts) + "]"
            raw_list.append({
                "id": el.get("id"),
                "tag": el.get("tag", "P"),
                "text": txt
            })

        prompt = f"""
You are an elite PDF Accessibility Remediation Specialist (PDF/UA-1 ISO 14289-1 & WCAG 2.1 AA).
Analyze this textbook page image and the detected raw items to produce the clean, unified semantic document structure.

Detected Raw Items:
{json.dumps(raw_list, indent=2)}

STRICT REMEDIATION RULES:
1. FORMULAS & DERIVATIONS (CRITICAL):
   - In multi-step derivations where equations are on separate vertical lines, each distinct line is its OWN separate <Formula> tag!
     For example:
     In Section 2.1 Example 1:
     - Line 1: 'd = \\sqrt{{[6 - (-4)]^2 + [-1 - (-5)]^2}}' is Formula Tag 1.
     - Line 2: 'd = \\sqrt{{(10)^2 + (4)^2}} = \\sqrt{{116}} = 2\\sqrt{{29}}' is Formula Tag 2.
     - Line 3: 'M = ((-4 + 6)/2, (-5 + (-1))/2) = (1, -3)' is Formula Tag 3.
     These 3 lines must be EXACTLY 3 separate <Formula> tags!
     In Section 2.2 Example 1 (Circles):
     - Line 1: '(x - h)^2 + (y - k)^2 = r^2' is Formula Tag 1.
     - Line 2: '(x - 3)^2 + [y - (-1/2)]^2 = (5)^2' is Formula Tag 2.
     - Line 3: '(x - 3)^2 + (y + 1/2)^2 = 25' is Formula Tag 3.
     These 3 lines must be EXACTLY 3 separate <Formula> tags!
     In Section 2.2 Example 2 (Circles):
     - Line 1: 'x^2 + y^2 - 12x + 8y + 5 = 0' is Formula Tag 1.
     - Line 2: 'x^2 - 12x + 36 + y^2 + 8y + 16 = -5 + 36 + 16' is Formula Tag 2.
     - Line 3: '(x - 6)^2 + (y + 4)^2 = 47' is Formula Tag 3.
     These 3 lines must be EXACTLY 3 separate <Formula> tags!
   - Fractions (numerator, fraction bar, denominator), radicals/square roots, exponents, and equations MUST each be a SINGLE UNIFIED <Formula> tag per line.
   - For every <Formula>, provide natural spoken 'alt_text' for screen readers.
   - Do NOT produce isolated tokens like 'd = √', '2', 'M (', '______', '2 2 )' as separate tags.
   - In derivations like x-intercept: emit 'x-intercept:' as <P>, then emit each equation step on its line as its own clean <Formula> tag ('x = y^2 - 4', 'x = (0)^2 - 4', 'x = -4').
   - In y-intercepts: emit 'y-intercepts:' as <P>, then emit each equation step ('x = y^2 - 4', '0 = y^2 - 4', 'y = 2 or y = -2').
2. PARAGRAPHS & PROBLEM PROMPTS (CRITICAL):
   - When an example has an introductory problem statement (e.g. 'Example 1: Write the standard form of an equation of the circle whose center is (3, -1/2) and whose radius is 5.'), that statement is a SINGLE <P> tag.
   - NEVER include the equations/derivations below it in the paragraph's raw_ids!
   - Sentences wrapping across lines must be merged into single unified <P> tags.
3. HEADINGS:
   - Chapter/Document title -> H1
   - Major section headers (e.g. 'SECTION 2.1 ...', 'SECTION 2.2 ...') -> H2
   - Section subheadings / Callout titles ('KEY CONCEPTS', 'EXAMPLES') -> H3
4. FIGURES & GRAPHS (CRITICAL):
   - Coordinate graphs and plots must be a single <Figure> tag with descriptive 'alt_text' describing the axes, curves, and plotted features.
   - Do NOT tag axis labels, numbers (-5, 0, 5, etc.), tick marks, or points ((0, 2), (-4, 0)) as separate text tags. Zero child text tags inside graphs!
5. TABLES (CRITICAL):
   - When a data table, grid of values, or coordinate table (such as a 2-row table of 'x' values and 'y' values, or any grid with rows and columns of data) is present, it MUST be tagged as a <Table>!
   - NEVER tag a data table or grid as a <P> paragraph!
   - Do NOT turn a table into a descriptive text sentence like 'Table of values for...'. It MUST remain as a Table element.
6. raw_ids:
   - In each element, list the 'raw_ids' of all detected raw items that belong to it so we can calculate the exact bounding box.

Return ONLY a JSON array of unified elements inside ```json ... ``` with schema:
[
  {{
    "type": "heading" | "paragraph" | "formula" | "table" | "figure" | "list_item",
    "tag": "H1" | "H2" | "H3" | "P" | "Formula" | "Table" | "Figure" | "LI",
    "heading_level": 1 | 2 | 3,
    "content": "exact unified text or math string",
    "alt_text": "screen reader spoken text (required for Formula and Figure, empty for others)",
    "raw_ids": [integer IDs of merged raw items]
  }}
]
"""
        MODELS = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest", "gemini-3.8-flash"]
        resp_text = None

        key = cls.get_current_key()
        if not key:
            return raw_elements

        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=key)

            for m in MODELS:
                try:
                    resp = client.models.generate_content(
                        model=m,
                        contents=[
                            types.Part.from_bytes(data=img_bytes, mime_type="image/png"),
                            prompt
                        ]
                    )
                    if resp and resp.text:
                        resp_text = resp.text
                        break
                except Exception as me:
                    if "429" in str(me) or "RESOURCE_EXHAUSTED" in str(me):
                        if len(cls.get_api_keys()) <= 1:
                            time.sleep(1.0)
                        cls._rotate_key()
                        key = cls.get_current_key()
                        client = genai.Client(api_key=key)
                        continue
        except Exception:
            pass

        if not resp_text:
            return raw_elements

        clean_text = resp_text.strip()
        if clean_text.startswith("```json"): clean_text = clean_text[7:]
        if clean_text.startswith("```"): clean_text = clean_text[3:]
        if clean_text.endswith("```"): clean_text = clean_text[:-3]

        try:
            unified_raw = json.loads(clean_text.strip())
        except Exception:
            return raw_elements

        if not isinstance(unified_raw, list) or len(unified_raw) < 3:
            return raw_elements

        def _calc_bbox_overlap(b1, b2):
            if not b1 or not b2 or len(b1) != 4 or len(b2) != 4:
                return 0.0
            x_left = max(b1[0], b2[0])
            y_bottom = max(b1[1], b2[1])
            x_right = min(b1[2], b2[2])
            y_top = min(b1[3], b2[3])
            if x_right <= x_left or y_top <= y_bottom:
                return 0.0
            intersection_area = (x_right - x_left) * (y_top - y_bottom)
            area1 = (b1[2] - b1[0]) * (b1[3] - b1[1])
            area2 = (b2[2] - b2[0]) * (b2[3] - b2[1])
            min_area = min(area1, area2)
            if min_area <= 0:
                return 0.0
            return intersection_area / min_area

        # Map bounding boxes & construct final elements
        raw_map = {el["id"]: el for el in raw_elements if "id" in el}
        detected_tables = [el for el in raw_elements if el.get("tag") in ("Table", "TABLE") or el.get("type") == "table"]
        used_table_ids = set()
        final_elements = []
        elem_id = 1

        # Locate any detected vector graph / figure bounding boxes
        detected_figs = list(figures_data) if figures_data else []

        for item in unified_raw:
            tag = item.get("tag", "P")
            t_type = item.get("type", "paragraph")
            content = (item.get("content") or "").strip()
            alt_text = (item.get("alt_text") or "").strip()
            h_level = item.get("heading_level")
            raw_ids = item.get("raw_ids", [])

            # Filter raw_ids: Paragraphs must NEVER swallow math equation raw items!
            matched_boxes = []
            for rid in raw_ids:
                if rid in raw_map and "bounding_box" in raw_map[rid]:
                    raw_el = raw_map[rid]
                    if tag in ("P", "H1", "H2", "H3", "LI"):
                        raw_txt = (raw_el.get("content") or "").strip()
                        raw_words = [w.lower() for w in re.findall(r'[a-zA-Z0-9]+', raw_txt) if len(w) > 1]
                        c_words = set(re.findall(r'[a-zA-Z0-9]+', content.lower()))
                        # If raw item has no word overlap with the paragraph text, reject it!
                        if raw_words and not any(w in c_words for w in raw_words):
                            continue
                    matched_boxes.append(raw_el["bounding_box"])

            # If Formula only matched a partial fragment (e.g. only '= 25' with width < 55),
            # search for unassigned raw items on that same horizontal band to encompass full equation (must be same column!)
            if tag == "Formula" and matched_boxes and (max(b[2] for b in matched_boxes) - min(b[0] for b in matched_boxes) < 55) and len(content) > 10:
                mid_y = (min(b[1] for b in matched_boxes) + max(b[3] for b in matched_boxes)) / 2.0
                ref_x = matched_boxes[0][0]
                is_right_col = ref_x >= page_w * 0.45
                for rid, r_el in raw_map.items():
                    r_box = r_el.get("bounding_box", [0, 0, 0, 0])
                    if r_box[0] > 0 and ((r_box[0] >= page_w * 0.45) == is_right_col) and abs(((r_box[1] + r_box[3]) / 2.0) - mid_y) <= 8.0:
                        matched_boxes.append(r_box)

            if matched_boxes:
                bbox = [
                    round(min(b[0] for b in matched_boxes), 2),
                    round(min(b[1] for b in matched_boxes), 2),
                    round(max(b[2] for b in matched_boxes), 2),
                    round(max(b[3] for b in matched_boxes), 2)
                ]
            else:
                # Text match fallback
                c_clean = content.lower()
                text_matches = [
                    el["bounding_box"] for el in raw_elements
                    if "bounding_box" in el and (el.get("content", "").lower() in c_clean or c_clean in el.get("content", "").lower())
                ]
                if text_matches:
                    bbox = [
                        round(min(b[0] for b in text_matches), 2),
                        round(min(b[1] for b in text_matches), 2),
                        round(max(b[2] for b in text_matches), 2),
                        round(max(b[3] for b in text_matches), 2)
                    ]
                else:
                    bbox = [0, 0, round(page_w, 2), round(page_h, 2)]

            if not content and tag not in ("Figure", "Table"):
                continue

            # Check if this item is or encompasses a detected genuine table
            matching_table = None
            for dt in detected_tables:
                if dt.get("id") in raw_ids:
                    matching_table = dt
                    break
                if dt.get("bounding_box") and _calc_bbox_overlap(bbox, dt["bounding_box"]) > 0.4:
                    matching_table = dt
                    break
            if not matching_table and (tag in ("Table", "TABLE") or "table of values" in content.lower()):
                for dt in detected_tables:
                    if dt.get("id") not in used_table_ids:
                        matching_table = dt
                        break

            if matching_table:
                # Preserve genuine table with all rows and cells!
                import copy
                tbl = copy.deepcopy(matching_table)
                tbl["id"] = elem_id
                elem_id += 1
                for r in tbl.get("kids", []):
                    r["id"] = elem_id
                    elem_id += 1
                    for c in r.get("kids", []):
                        c["id"] = elem_id
                        elem_id += 1
                used_table_ids.add(matching_table.get("id"))
                final_elements.append(tbl)
                continue

            if tag == "Figure" and detected_figs:
                # Assign exact coordinate graph bounding box
                cur_fig = detected_figs.pop(0)
                f_box = cur_fig.get("bbox") or cur_fig.get("figure_el", {}).get("bounding_box")
                if f_box:
                    bbox = f_box
                if not alt_text and cur_fig.get("figure_el", {}).get("alt_text"):
                    alt_text = cur_fig["figure_el"]["alt_text"]

            # Add comfortable 2.5pt padding to Formula bounding boxes so leading variables (x) and exponents (^2) are cleanly framed
            if tag == "Formula" and bbox[2] > bbox[0]:
                bbox = [
                    round(max(0, bbox[0] - 2.5), 2),
                    round(max(0, bbox[1] - 2.0), 2),
                    round(min(page_w, bbox[2] + 2.5), 2),
                    round(min(page_h, bbox[3] + 2.0), 2)
                ]

            el_obj = {
                "id": elem_id,
                "type": t_type,
                "tag": tag,
                "content": content,
                "bounding_box": bbox,
                "confidence": 1.0
            }
            if h_level and (tag in ("H1", "H2", "H3", "H4", "H5", "H6") or t_type == "heading"):
                el_obj["heading_level"] = h_level
            if alt_text:
                el_obj["alt_text"] = alt_text

            final_elements.append(el_obj)
            elem_id += 1

        # Insert any unassigned detected tables at their correct vertical positions
        for dt in detected_tables:
            if dt.get("id") not in used_table_ids:
                tb = dt.get("bounding_box", [0, 0, 0, 0])
                inserted = False
                for idx, fe in enumerate(final_elements):
                    fb = fe.get("bounding_box", [0, 0, 0, 0])
                    if tb[3] > fb[3]:
                        import copy
                        tbl = copy.deepcopy(dt)
                        tbl["id"] = elem_id
                        elem_id += 1
                        for r in tbl.get("kids", []):
                            r["id"] = elem_id
                            elem_id += 1
                            for c in r.get("kids", []):
                                c["id"] = elem_id
                                elem_id += 1
                        final_elements.insert(idx, tbl)
                        used_table_ids.add(dt.get("id"))
                        inserted = True
                        break
                if not inserted:
                    import copy
                    tbl = copy.deepcopy(dt)
                    tbl["id"] = elem_id
                    elem_id += 1
                    for r in tbl.get("kids", []):
                        r["id"] = elem_id
                        elem_id += 1
                        for c in r.get("kids", []):
                            c["id"] = elem_id
                            elem_id += 1
                    final_elements.append(tbl)
                    used_table_ids.add(dt.get("id"))

        # Clamping: Ensure paragraph bounding box never swallows subsequent formulas or figures
        for i in range(len(final_elements) - 1):
            el = final_elements[i]
            next_el = final_elements[i + 1]
            if el.get("tag") in ("P", "H1", "H2", "H3", "LI") and next_el.get("tag") in ("Formula", "Figure"):
                b_curr = el.get("bounding_box", [0, 0, 0, 0])
                b_next = next_el.get("bounding_box", [0, 0, 0, 0])
                x_overlap = min(b_curr[2], b_next[2]) - max(b_curr[0], b_next[0])
                # Only clamp if in the same column, vertically adjacent, and b_next is below b_curr top
                if x_overlap > 10 and b_curr[1] < b_next[3] and b_next[3] < b_curr[3] - 8:
                    el["bounding_box"][1] = round(max(b_curr[1], b_next[3] + 1.5), 2)

        # Post-processing: Merge only broken mid-line fragments (e.g. "= \sqrt{116} = 2\sqrt{29}")
        # Distinct equation lines (each starting with a variable like 'd =', 'M =', 'x =') must remain separate Formula tags!
        merged_formula_elements = []
        for el in final_elements:
            if merged_formula_elements and el.get("tag") == "Formula" and merged_formula_elements[-1].get("tag") == "Formula":
                curr_txt = el.get("content", "").strip()
                prev_txt = merged_formula_elements[-1].get("content", "").strip()
                b_prev = merged_formula_elements[-1].get("bounding_box", [0, 0, 0, 0])
                b_curr = el.get("bounding_box", [0, 0, 0, 0])

                # A new equation step has its own variable on the left, e.g. "d =", "M =", "x =", "y ="
                starts_with_new_var = bool(re.match(r'^(?:[a-zA-Z]|[A-Za-z]\([a-zA-Z0-9,\s]+\))\s*=', curr_txt))

                # Check vertical overlap to see if they are truly on the exact same horizontal line
                y_overlap = max(0, min(b_prev[3], b_curr[3]) - max(b_prev[1], b_curr[1]))
                min_h = min(max(b_prev[3] - b_prev[1], 1), max(b_curr[3] - b_curr[1], 1))
                is_exact_same_line = (y_overlap / min_h) >= 0.70

                # Only merge if it's a mid-line continuation (e.g. starts with '=') AND not a new variable step AND on the same horizontal line
                is_orphan_continuation = curr_txt.startswith(('=', '≈', '≠', '≤', '≥')) and not starts_with_new_var

                if is_orphan_continuation and is_exact_same_line:
                    merged_formula_elements[-1]["content"] = f"{prev_txt} {curr_txt}".strip()
                    merged_formula_elements[-1]["bounding_box"] = [
                        min(b_prev[0], b_curr[0]), min(b_prev[1], b_curr[1]),
                        max(b_prev[2], b_curr[2]), max(b_prev[3], b_curr[3])
                    ]
                    if el.get("alt_text"):
                        prev_alt = merged_formula_elements[-1].get("alt_text", "")
                        merged_formula_elements[-1]["alt_text"] = f"{prev_alt} {el.get('alt_text')}".strip()
                    continue
            merged_formula_elements.append(el)
        final_elements = merged_formula_elements

        # Wrap every Formula into a <P> container, exactly matching Miller_CA3e_Ch02_DetailedSummary_A11y.pdf
        wrapped_formula_elements = []
        for el in final_elements:
            if el.get("tag") == "Formula" or el.get("type") == "formula":
                el.pop("heading_level", None)
                f_id = el.get("id")
                p_wrap = {
                    "id": f"{f_id}-P" if f_id is not None else None,
                    "type": "paragraph",
                    "tag": "P",
                    "content": el.get("content", ""),
                    "alt_text": el.get("alt_text", ""),
                    "bounding_box": el.get("bounding_box"),
                    "kids": [el],
                    "confidence": 1.0
                }
                wrapped_formula_elements.append(p_wrap)
            else:
                wrapped_formula_elements.append(el)
        final_elements = wrapped_formula_elements

        # Safeguard: Ensure every single element and child node on this page has a strictly unique ID
        seen_ids = set()
        next_id = elem_id + 1
        def _dedup_ids(nodes):
            nonlocal next_id
            for n in nodes:
                nid = str(n.get("id")) if n.get("id") is not None else None
                if nid is None or nid in seen_ids:
                    n["id"] = next_id
                    seen_ids.add(str(next_id))
                    next_id += 1
                else:
                    seen_ids.add(nid)
                if n.get("kids"):
                    _dedup_ids(n["kids"])
        _dedup_ids(final_elements)

        # Cache the successful remediation
        try:
            cls._memory_cache[cache_key] = json.dumps(final_elements)
            cls._save_cache()
        except Exception:
            pass

        return final_elements
