import os
import json
import re
import time
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '../.env'))
# NEW
XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_URL = "https://api.groq.com/openai/v1/chat/completions"




# ─────────────────────────────────────────────────────────────────────────────
# PROMPT 1 — New analysis agent using student_info_json schema
# Used by: student_summary_new(student_info_json)
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT_ANALYSIS_NEW = """
    You are an academic-risk analysis engine embedded in a student-analytics platform.
    You receive a structured student_info_json object and output a single JSON object.
    You never output prose, markdown, explanations, or anything outside the JSON.

    ━━━ INPUT SCHEMA ━━━
    student_name            : str   — the student's full name
    risk_score              : float — current composite risk score (0–100)
    risk_score_definition   : str   — formula/description of how risk_score is computed
    effort                  : float — current effort score (0–100); formula described in field
    effort_definition       : str   — formula/description of how effort is computed
    academic_performance    : float — current academic performance score (0–100)
    academic_performance_definition : str — formula/description
    lag_score               : float — effort-to-performance conversion gap (0–100); higher = worse
    lag_score_definition    : str   — formula/description
    risk_of_detention       : float — detention risk score (0–100)
    risk_of_detention_definition : str — formula/description
    sem_week                : int   — current semester week (used to contextualise urgency)
    midterm_week            : int   — week when midterm exam occurs (typically 18)
    endterm_week            : int   — week when endterm exam occurs (typically 19)
    reason_of_flagging      : str   — pipe-separated breakdown of risk score signals,
                                      e.g. "assn_streak:2|high_risk_streak:3|et_drop:15"
    class_avg_effort        : float — class average effort score this week
    class_avg_performance   : float — class average academic performance this week
    avg_effort_8w           : float — this student's average effort over up to 8 weeks
    avg_performance_5w      : float — this student's average academic performance over last 5 weeks
    midterm_score           : float | false — actual midterm score if available (week > 8 and < 18), else false
    endterm_score           : float | false — actual endterm score if available (even-sem week 4–7), else false
    
    ━━━ CONTEXTUAL URGENCY RULES ━━━
    The same risk_score means different things depending on sem_week:
    • Week ≤ 5  : Early semester — low scores are common; only escalate if multiple signals fire.
    • Week 6–10 : Mid-semester — sustained poor performance is a strong signal.
    • Week 11–17: Pre-exam window — urgency is amplified; any risk_score > 40 warrants action.
    • Week 18–19: Exam weeks — direct intervention may be too late; focus on triage.
    • Weeks approaching midterm_week or endterm_week within 3 weeks → raise urgency by one notch.

    ━━━ SIGNAL PARSING ━━━
    Parse reason_of_flagging (pipe-separated key:value pairs) to identify which signals fired
    and their magnitudes. Common signal keys:
        assn_streak         — consecutive weeks of missed/incomplete assignments
        quiz_streak         — consecutive weeks of missed quizzes
        high_risk_streak    — consecutive weeks at high risk (≥50)
        risk_of_detention   — detention risk score
        lag_score_penalty   — effort-to-performance gap
        avg_risk_score_3w   — average risk score over 3 weeks
        avg_at_3w           — average academic performance (3w)
        avg_et_3w           — average effort score (3w)
        et_drop             — effort score drop in pp

    ━━━ DERIVED COMPARISONS ━━━
    effort_gap      = class_avg_effort − effort        (positive = student below class)
    performance_gap = class_avg_performance − academic_performance  (positive = below class)
    long_run_effort_gap = class_avg_effort − avg_effort_8w
    long_run_perf_gap   = class_avg_performance − avg_performance_5w
    lag_severity    = "high" if lag_score > 60 else "moderate" if lag_score > 30 else "low"

    ━━━ INTERVENTION SELECTION ━━━
    Possible interventions (select at most one primary + one secondary):
        monitor             — no action needed; observe next week
        email_student       — send a supportive check-in email to the student
        one_to_one_check    — schedule a face-to-face check-in meeting
        email_parent        — contact parent/guardian (use only if situation is very severe:
                              Tier 1 equivalent, or risk_score > 75, or 3+ signals fired)
        refer_to_counsellor — escalate to student counsellor (use if sustained multi-week
                              crisis or student shows signs of disengagement + absenteeism)

    Decision tree (top-to-bottom, first match wins):
    1. refer_to_counsellor  IF risk_score > 75 AND (assn_streak ≥ 3 OR high_risk_streak ≥ 3)
                              OR effort < 20 AND academic_performance < 30
    2. email_parent         IF risk_score > 70 OR (risk_score > 55 AND sem_week ≥ 14)
                              (can be secondary alongside refer_to_counsellor)
    3. one_to_one_check     IF risk_score > 45 OR any streak signal ≥ 2
    4. email_student        IF risk_score 25–45 OR single signal fired
    5. monitor              DEFAULT (risk is low or too early to act)

    ━━━ TALKING POINTS ━━━
    Identify 3–6 specific, actionable talking points for the communication. These should:
    - Reference specific signals from reason_of_flagging (with humanised descriptions)
    - Mention the effort/performance gap relative to the class where notable
    - Be phrased as advisor notes, not as direct student-facing text
    - If midterm_score or endterm_score is available, include it as context
    - NOT apply to the "monitor" case (set to empty list)

    ━━━ OUTPUT SCHEMA ━━━
    Return ONLY this JSON object. No extra keys, no markdown, no comments.

    {
      "recommended_intervention": "<monitor | email_student | one_to_one_check | email_parent | refer_to_counsellor>",
      "secondary_intervention": "<one of the above | null>",
      "reasoning": "<2–4 sentences explaining why this intervention was chosen, citing sem_week context, specific signals, and risk trajectory>",
      "urgency": "<low | moderate | high | critical>",
      "tone": "<supportive | urgent | neutral>",
      "talking_points": [
        "<specific point 1>",
        "<specific point 2>",
        "..."
      ],
      "email_student_brief": "<1–2 sentence brief summarising what the student email should convey | null if not applicable>",
      "email_parent_brief": "<1–2 sentence brief for parent communication | null if not applicable>",
      "counsellor_brief": "<1–2 sentence referral summary for the counsellor | null if not applicable>",
      "signals_to_highlight": ["<key data point 1>", "<key data point 2>", "..."]
    }
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT 2 — Content generation agent
# Used by: generate_content(content_type, content_generation_command)
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT_CONTENT = """
    You are a compassionate communication specialist for an academic support platform.
    You receive a content_generation_command (produced by the analysis agent) and a content_type.
    You write the requested document and output ONLY the document text — no meta-commentary,
    no JSON wrapper, no subject lines unless the format requires one.

    ━━━ CONTENT TYPES ━━━

    email_to_parent
    Format : Professional email (Subject: / Body:)
    Tone   : Use the tone field. "urgent" → clear concern, action required.
                "supportive" → warm, collaborative, no alarm.
    Length : 150–250 words.
    Must include:
        • Specific signals from signals_to_highlight (paraphrased, not raw numbers)
        • The intervention being taken and what the parent can do at home
        • A clear next-step CTA (e.g., "Please call the advisor office by Friday")
    Never: use jargon (E_t, A_t, tier names), blame the student, or make medical claims.

    email_to_student
    Format : Friendly but direct email (Subject: / Body:)
    Tone   : Warm and non-judgmental regardless of urgency level.
    Length : 120–200 words.
    Must include:
        • Acknowledgement of specific pattern noticed (from signals_to_highlight)
        • One concrete offer (meeting, resource, study group — match the primary intervention)
        • Encouragement grounded in effort, not empty praise
    Never: threaten consequences, mention parents, reference scoring tiers.

    one_to_one_conversation
    Format : Numbered list of advisor talking points / open questions
    Tone   : Conversational and non-confrontational.
    Must include:
        • An opening check-in question (non-academic, builds rapport)
        • All questions derived from the talking points, reworded naturally
        • A closing commitment question (e.g., "What's one thing we can try this week?")
    Length : 6–10 items.

    counsellor_report
    Format : Structured professional report with these sections:
                Referral Reason | Observed Indicators | Risk Level | Recommended Focus Areas
    Tone   : Clinical, neutral, factual.
    Length : 200–350 words.
    Must include:
        • counsellor_brief as the Referral Reason
        • Signals from signals_to_highlight as Observed Indicators
        • Risk level mapped from urgency (critical → High, high → High, moderate → Moderate, low → Low)
    Never: speculate on diagnosis, use first-person, or include student's full name.

    ━━━ RULES ━━━
    - Match the tone field exactly (supportive / urgent / neutral).
    - Highlight only the signals listed in signals_to_highlight.
    - Do not invent data or reference scores not provided.
    - If content_type is not one of the four above, reply with exactly:
        ERROR: unsupported content_type. Must be one of:
        email_to_parent | email_to_student | one_to_one_conversation | counsellor_report
""".strip()


VALID_CONTENT_TYPES = {
    "email_to_parent",
    "email_to_student",
    "one_to_one_conversation",
    "counsellor_report",
}


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

# NEW
def _call_gemini(system_prompt: str, user_message: str, temperature: float = 0.2) -> str:
    """
    Calls xAI Grok API (OpenAI-compatible). Retries on 429 with exponential backoff.
    """
    if not XAI_API_KEY:
        raise EnvironmentError("XAI_API_KEY environment variable is not set.")

    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_message},
        ],
        "temperature": temperature,
    }

    MAX_RETRIES = 4
    backoff = 5

    for attempt in range(MAX_RETRIES):
        response = requests.post(
            XAI_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {XAI_API_KEY}",
            },
            json=payload,
            timeout=60,
        )

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            wait = int(retry_after) if retry_after else backoff
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)
                backoff *= 2
                continue
            response.raise_for_status()

        if not response.ok:
            print(f"xAI error {response.status_code}: {response.text}")
        response.raise_for_status()
        break

    data = response.json()

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise ValueError(f"Unexpected xAI response structure: {data}") from exc

def _extract_json(raw: str) -> dict:
    """Strips markdown fences then parses JSON."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    return json.loads(cleaned)




# ─────────────────────────────────────────────────────────────────────────────
# FUNCTION 1b — student_summary_new  (new student_info_json schema)
# Called by the new student_summary_view endpoint in views.py
# ─────────────────────────────────────────────────────────────────────────────

def student_summary_new(student_info_json: dict) -> dict:
    """
    Analyses a flagged student using the richer student_info_json schema and
    returns a structured recommendation with intervention, talking points,
    and content-generation briefs.

    Args:
        student_info_json (dict): Must contain:
            student_name            (str)
            risk_score              (float, 0–100)
            risk_score_definition   (str)
            effort                  (float, 0–100)
            effort_definition       (str)
            academic_performance    (float, 0–100)
            academic_performance_definition (str)
            lag_score               (float, 0–100)
            lag_score_definition    (str)
            risk_of_detention       (float, 0–100)
            risk_of_detention_definition (str)
            sem_week                (int)
            midterm_week            (int)   — typically 18
            endterm_week            (int)   — typically 19
            reason_of_flagging      (str)   — pipe-separated signal:value pairs
            class_avg_effort        (float)
            class_avg_performance   (float)
            avg_effort_8w           (float) — student's avg effort up to 8 weeks
            avg_performance_5w      (float) — student's avg performance last 5 weeks
            midterm_score           (float | false)
            endterm_score           (float | false)

    Returns:
        dict with keys:
            recommended_intervention  (str)
            secondary_intervention    (str | null)
            reasoning                 (str)
            urgency                   (str: low | moderate | high | critical)
            tone                      (str: supportive | urgent | neutral)
            talking_points            (list[str])
            email_student_brief       (str | null)
            email_parent_brief        (str | null)
            counsellor_brief          (str | null)
            signals_to_highlight      (list[str])

    Raises:
        EnvironmentError:    GEMINI_API_KEY not set.
        requests.HTTPError:  API call failed.
        ValueError:          Response could not be parsed as JSON.
    """
    user_message = (
        "Analyse the following student data and return the JSON response "
        "exactly as specified in your instructions.\n\n"
        f"student_info_json = {json.dumps(student_info_json, indent=2)}"
    )

    raw = _call_gemini(
        system_prompt=SYSTEM_PROMPT_ANALYSIS_NEW,
        user_message=user_message,
        temperature=0.1,
    )

    return _extract_json(raw)


# ─────────────────────────────────────────────────────────────────────────────
# FUNCTION 2 — generate_content
# Called by the generate_content_view endpoint in views.py
# ─────────────────────────────────────────────────────────────────────────────

def generate_content(content_type: str, student_name: str, ai_analysis: dict) -> str:
    """
    Generates a human-facing communication document based on the AI analysis
    produced by student_summary_new().

    Args:
        content_type (str): One of:
            "email_to_parent" | "email_to_student" |
            "one_to_one_conversation" | "counsellor_report"

        student_name (str): The student's full name (for personalisation).

        ai_analysis (dict): The full dict returned by student_summary_new().
            Used fields:
                talking_points          (list[str])
                signals_to_highlight    (list[str])
                tone                    (str)
                email_student_brief     (str | null)
                email_parent_brief      (str | null)
                counsellor_brief        (str | null)
                recommended_intervention (str)
                urgency                 (str)

    Returns:
        str: The generated document text, ready for advisor review.

    Raises:
        ValueError:          Unsupported content_type (validated before API call).
        EnvironmentError:    GEMINI_API_KEY not set.
        requests.HTTPError:  API call failed.
    """
    if content_type not in VALID_CONTENT_TYPES:
        raise ValueError(
            f"Unsupported content_type '{content_type}'. "
            f"Must be one of: {', '.join(sorted(VALID_CONTENT_TYPES))}"
        )

    # Build the content_generation_command from the ai_analysis output
    content_generation_command = {
        "student_name":           student_name,
        "content_type":           content_type,
        "talking_points":         ai_analysis.get("talking_points", []),
        "signals_to_highlight":   ai_analysis.get("signals_to_highlight", []),
        "tone":                   ai_analysis.get("tone", "supportive"),
        "email_student_brief":    ai_analysis.get("email_student_brief"),
        "email_parent_brief":     ai_analysis.get("email_parent_brief"),
        "counsellor_brief":       ai_analysis.get("counsellor_brief"),
        "recommended_intervention": ai_analysis.get("recommended_intervention"),
        "urgency":                ai_analysis.get("urgency", "moderate"),
    }

    user_message = (
        f"content_type: {content_type}\n\n"
        f"content_generation_command:\n"
        f"{json.dumps(content_generation_command, indent=2)}\n\n"
        "Write the document now. Output only the document — no preamble."
    )

    return _call_gemini(
        system_prompt=SYSTEM_PROMPT_CONTENT,
        user_message=user_message,
        temperature=0.6,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FUNCTION 3 — class_summary  (unchanged from original)
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT_CLASS = """
    You are an academic analytics assistant. You receive a JSON summary of a class's
    performance metrics for a given semester week and return a concise 3–5 sentence
    narrative summary for the class advisor. Focus on risk distribution, effort and
    performance trends, and any patterns in the top diagnoses. Be specific and actionable.
    Output only the narrative text — no JSON, no markdown.
""".strip()


def class_summary(input_prompt: dict) -> str:
    """
    Generates a class-level narrative summary for the advisor dashboard.
    Called by class_summary_view in views.py.
    """
    user_message = (
        "Generate a concise class summary for the advisor.\n\n"
        f"class_data = {json.dumps(input_prompt, indent=2)}"
    )

    return _call_gemini(
        system_prompt=SYSTEM_PROMPT_CLASS,
        user_message=user_message,
        temperature=0.4,
    )
