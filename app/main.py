import os
import json
from typing import Annotated, Literal, List
from dotenv import load_dotenv

from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.tools import tool
from tavily import TavilyClient
import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict
from langgraph.prebuilt import ToolNode, tools_condition

# Load environment variables (e.g. GOOGLE_API_KEY)
load_dotenv()

# Path to lightweight local JSON memory file
MEMORY_FILE_PATH = os.path.join(os.path.dirname(__file__), "research_memory.json")

def load_memory() -> List[dict]:
    """Loads persistent research lessons from the JSON memory file."""
    if not os.path.exists(MEMORY_FILE_PATH):
        return []
    try:
        with open(MEMORY_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Failed to load research memory: {e}")
        return []

def save_memory(memory_data: List[dict]) -> None:
    """Saves research lessons to the human-readable JSON memory file."""
    try:
        with open(MEMORY_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error saving research memory: {e}")

def add_lessons_to_memory(new_lessons: List[dict]) -> tuple:
    """Appends new unique lessons to persistent memory avoiding duplicates.
    Returns tuple: (added_lessons, duplicate_lessons)"""
    existing_memory = load_memory()
    existing_lessons_normalized = {
        item.get("lesson", "").strip().lower() for item in existing_memory
    }
    
    added = []
    duplicates = []
    for lesson_item in new_lessons:
        norm_lesson = lesson_item.get("lesson", "").strip().lower()
        if norm_lesson and norm_lesson not in existing_lessons_normalized:
            existing_memory.append(lesson_item)
            existing_lessons_normalized.add(norm_lesson)
            added.append(lesson_item)
        else:
            duplicates.append(lesson_item)
            
    if added:
        save_memory(existing_memory)
    return added, duplicates

# 1. Define the tools
@tool
def calculator(a: float, b: float, operation: str) -> float:
    """Performs basic arithmetic operations. Operation can be 'add', 'subtract', 'multiply', or 'divide'."""
    if operation == "add":
        return a + b
    elif operation == "subtract":
        return a - b
    elif operation == "multiply":
        return a * b
    elif operation == "divide":
        return a / b
    return 0.0

@tool
def web_search(query: str) -> str:
    """Searches the web for information using Tavily. Use this when you need current or external information."""
    tavily_api_key = os.environ.get("TAVILY_API_KEY")
    if not tavily_api_key:
        return "Error: TAVILY_API_KEY environment variable not set."
    
    client = TavilyClient(api_key=tavily_api_key)
    try:
        response = client.search(query=query)
        results = response.get("results", [])
        if not results:
            return "No results found."
        
        formatted_results = []
        for res in results:
            title = res.get("title", "No Title")
            url = res.get("url", "No URL")
            content = res.get("content", "No Content")
            formatted_results.append(f"Title: {title}\nURL: {url}\nContent: {content}\n")
            
        return "\n".join(formatted_results)
    except Exception as e:
        return f"Error during web search: {str(e)}"

@tool
def fetch_page(url: str) -> str:
    """Fetches the content of a webpage given an exact URL and returns clean readable text along with metadata. Use this to read full content of relevant search results."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
    }
    try:
        response = requests.get(url, timeout=10, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract title
        title = soup.title.string.strip() if soup.title and soup.title.string else "No Title Found"
        
        # Remove scripts, styles, nav, footer, header to get main content
        for element in soup(["script", "style", "nav", "footer", "header"]):
            element.extract()
            
        text = soup.get_text(separator=' ', strip=True)
        clean_text = ' '.join(text.split())[:10000]
        
        if not clean_text:
            return (
                f"[FETCH STATUS: FAILED]\n"
                f"URL: {url}\n"
                f"REASON: Extracted page content was empty.\n"
                f"NOTE: Do not use this URL as verified page evidence."
            )
            
        return (
            f"[FETCH STATUS: SUCCESS]\n"
            f"URL: {url}\n"
            f"TITLE: {title}\n"
            f"CONTENT:\n{clean_text}"
        )
    except requests.exceptions.RequestException as e:
        return (
            f"[FETCH STATUS: FAILED]\n"
            f"URL: {url}\n"
            f"REASON: HTTP Request failed ({str(e)})\n"
            f"NOTE: Do not use this URL as verified page evidence."
        )
    except Exception as e:
        return (
            f"[FETCH STATUS: FAILED]\n"
            f"URL: {url}\n"
            f"REASON: Unexpected error ({str(e)})\n"
            f"NOTE: Do not use this URL as verified page evidence."
        )

# 2. Define Data Structures & State
class Claim(BaseModel):
    original_claim: str = Field(
        description="The exact original factual assertion or question stated by the user. Must NOT be rewritten or modified based on search evidence."
    )
    evidence: str = Field(
        description="Direct snippet, quote, or summarized information found in the sources regarding the claim"
    )
    source_url: str = Field(description="The exact URL of the source for this claim")
    source_title: str = Field(description="The title of the source page/article")
    evidence_type: Literal["fetched_page", "search_snippet"] = Field(
        description="Must be 'fetched_page' ONLY if content was successfully retrieved via fetch_page tool; otherwise 'search_snippet'"
    )
    verification_status: Literal["unverified", "fetched", "audited_supported", "audited_contradicted", "failed"] = Field(
        description="'unverified' if based only on search snippet, 'fetched' if retrieved via successful fetch_page, 'audited_supported'/'audited_contradicted' after auditor evaluation, 'failed' if fetch failed"
    )

class ClaimsExtraction(BaseModel):
    claims: List[Claim]

class AuditRecord(BaseModel):
    original_claim: str = Field(description="The original user claim evaluated by the Auditor")
    source_url: str = Field(description="The cited source URL")
    evidence_type: Literal["fetched_page", "search_snippet"] = Field(
        description="The evidence type of the claim ('fetched_page' or 'search_snippet')"
    )
    verification_status: Literal["unverified", "fetched", "audited_supported", "audited_contradicted", "failed"] = Field(
        description="The updated verification status after auditor evaluation"
    )
    verdict: Literal["supported", "unsupported", "contradicted"] = Field(
        description="Must be 'supported' if cited source actually supports original_claim, 'contradicted' if source directly conflicts with original_claim, or 'unsupported' if source lacks sufficient evidence or fetch failed"
    )
    reasoning: str = Field(description="Independent audit reasoning explaining the verdict for the original claim")
    supporting_evidence: str = Field(
        description="Direct text snippet/excerpt quote extracted from the independently fetched source page, or explanation if unavailable"
    )

class ResearchLesson(BaseModel):
    lesson: str = Field(description="Concise, actionable rule/directive to improve future research")
    reason: str = Field(description="Explanation of why this lesson was derived from the audit results")
    source_of_feedback: Literal["auditor"] = Field(default="auditor", description="Source of feedback, set to 'auditor'")

class FeedbackOutput(BaseModel):
    lessons: List[ResearchLesson]

class State(TypedDict):
    # `add_messages` appends new messages to the existing list rather than overwriting them
    messages: Annotated[list, add_messages]
    claims: list[dict]
    audit_results: list[dict]
    feedback_lessons: list[dict]

# 3. Initialize model and bind tools
tools = [calculator, web_search, fetch_page]
llm = ChatGoogleGenerativeAI(model="gemini-3.5-flash-lite", temperature=0)
llm_with_tools = llm.bind_tools(tools)

BASE_SYSTEM_PROMPT = (
    "You are a meticulous research analyst assistant.\n"
    "When answering user queries using your tools, strictly follow these evidence rules:\n"
    "1. When performing web searches, preserve and use exact URLs returned by the search tool.\n"
    "2. If fetch_page is not executed or fails (returns `[FETCH STATUS: FAILED]`), DO NOT describe that evidence as fetched or verified. You MUST explicitly state that the source page could not be independently fetched/verified.\n"
    "3. Categorize all information in your response strictly using ONLY the following language labels:\n"
    "   - Use 'Information from Search Snippets' for any evidence obtained via web_search where fetch_page was not run or failed.\n"
    "   - Use 'Information from Fetched Page Content' ONLY when fetch_page actually succeeded and returned page content.\n"
    "   - Use 'Auditor-verified evidence' ONLY if the Auditor successfully fetched the source page and produced a supported or contradicted verdict.\n"
    "4. NEVER describe search snippet evidence or failed page fetches as 'verified' or 'verified from fetched page content'.\n"
    "5. NEVER infer or claim that a search result is verified merely because the URL points to a primary source (e.g. SEC.gov, EDGAR, government domains, official corporate sites).\n"
    "6. If a calculation was performed, include the calculation details."
)

# Deterministic mapping from structured verification_status -> final evidence label.
# This is the single source of truth for how evidence is described to the user.
# It must never be derived from LLM wording, domain name, or source title.
EVIDENCE_STATUS_LABELS = {
    "unverified": "Information from Search Snippets",
    "fetched": "Information from Fetched Page Content",
    "audited_supported": "Auditor-verified evidence (SUPPORTED)",
    "audited_contradicted": "Auditor-verified evidence (CONTRADICTED by the cited source)",
    "failed": "Unverified - source could not be independently fetched or verified",
}
DEFAULT_EVIDENCE_LABEL = "Unverified - status unknown"

def get_evidence_label(verification_status: str) -> str:
    """Deterministically resolves a claim's verification_status to its user-facing evidence label."""
    return EVIDENCE_STATUS_LABELS.get(verification_status, DEFAULT_EVIDENCE_LABEL)

# 4. Define Graph Nodes
def analyze_tool_history(messages: list) -> dict:
    """Scans message history for tool calls and outputs deterministic evidence status per URL."""
    successfully_fetched = set()
    failed_fetched = set()
    search_snippet_urls = set()

    for msg in messages:
        if hasattr(msg, "type") and msg.type == "tool":
            content = str(msg.content)
            if "[FETCH STATUS: SUCCESS]" in content:
                for line in content.split("\n"):
                    if line.startswith("URL:"):
                        url = line.replace("URL:", "").strip()
                        if url:
                            successfully_fetched.add(url)
            elif "[FETCH STATUS: FAILED]" in content:
                for line in content.split("\n"):
                    if line.startswith("URL:"):
                        url = line.replace("URL:", "").strip()
                        if url:
                            failed_fetched.add(url)
            elif "URL:" in content or "http" in content:
                for line in content.split("\n"):
                    if line.startswith("URL:"):
                        url = line.replace("URL:", "").strip()
                        if url:
                            search_snippet_urls.add(url)

    unverified_urls = (search_snippet_urls | failed_fetched) - successfully_fetched

    return {
        "successfully_fetched": list(successfully_fetched),
        "unverified_urls": list(unverified_urls)
    }

def chatbot(state: State):
    """The main LLM node that decides whether to answer or call a tool, incorporating learned research memory lessons."""
    memory_lessons = load_memory()
    tool_status = analyze_tool_history(state["messages"])
    
    # Construct evidence manifest for response synthesis
    if tool_status["successfully_fetched"] or tool_status["unverified_urls"]:
        fetched_str = "\n".join(f"- {u}" for u in tool_status["successfully_fetched"]) if tool_status["successfully_fetched"] else "- None"
        unverified_str = "\n".join(f"- {u}" for u in tool_status["unverified_urls"]) if tool_status["unverified_urls"] else "- None"
        
        manifest_section = (
            f"\n\n### DETERMINISTIC EVIDENCE STATUS MANIFEST (STRICT COMPLIANCE REQUIRED):\n"
            f"1. SUCCESSFULLY FETCHED URLs:\n{fetched_str}\n"
            f"   (ONLY content from these exact URLs may be described under 'Information from Fetched Page Content'.)\n\n"
            f"2. UNVERIFIED / FAILED FETCH URLs:\n{unverified_str}\n"
            f"   (Content from these URLs MUST ONLY be described under 'Information from Search Snippets'. You MUST explicitly state that these source pages could not be independently fetched/verified.)\n\n"
            f"STRICT RULES:\n"
            f"- If a source URL is under 'UNVERIFIED / FAILED FETCH URLs', NEVER describe it under 'Information from Fetched Page Content', even if it is a primary domain (e.g. SEC.gov, EDGAR, GuruFocus, Microsoft.com).\n"
            f"- If 'SUCCESSFULLY FETCHED URLs' is None, list '*None*' under 'Information from Fetched Page Content'."
        )
    else:
        manifest_section = ""

    if memory_lessons:
        lessons_formatted = "\n".join(
            f"- {item['lesson']} (Reason: {item['reason']})" for item in memory_lessons
        )
        system_content = (
            f"{BASE_SYSTEM_PROMPT}{manifest_section}\n\n"
            f"### RELEVANT LESSONS FROM PREVIOUS AUDITS:\n"
            f"Follow these guidelines derived from past audit feedback:\n"
            f"{lessons_formatted}"
        )
    else:
        system_content = f"{BASE_SYSTEM_PROMPT}{manifest_section}"

    messages = [SystemMessage(content=system_content)] + state["messages"]
    return {"messages": [llm_with_tools.invoke(messages)]}

def extract_claims(state: State):
    """Parses conversation history into structured claims and updates the graph state."""
    structured_llm = llm.with_structured_output(ClaimsExtraction)
    extraction_prompt = (
        "Analyze the entire conversation history above. Extract factual research claims into structured records.\n"
        "Follow these strict rules:\n"
        "1. Set 'original_claim' to the EXACT statement, question, or assertion made by the user in their input message. NEVER rewrite, modify, or correct 'original_claim' based on research findings or search evidence.\n"
        "2. Set 'evidence' to the snippet, quote, or information found in search/fetch results.\n"
        "3. Set 'evidence_type' to 'fetched_page' ONLY if content comes from a successful `fetch_page` tool call in history; otherwise set to 'search_snippet'.\n"
        "4. Set 'verification_status' strictly based on evidence source:\n"
        "   - Set to 'fetched' if content comes from a successful `fetch_page` tool call.\n"
        "   - Set to 'failed' if a `fetch_page` call was attempted but failed/returned `[FETCH STATUS: FAILED]`.\n"
        "   - Set to 'unverified' if based only on web_search snippets without a successful fetch."
    )
    messages = state["messages"] + [HumanMessage(content=extraction_prompt)]
    try:
        result = structured_llm.invoke(messages)
        extracted = [claim.model_dump() for claim in result.claims] if result and result.claims else []
    except Exception as e:
        extracted = []
    return {"claims": extracted}

def auditor(state: State):
    """Independently evaluates each extracted Analyst claim by re-inspecting cited sources."""
    claims = state.get("claims", [])
    if not claims:
        return {"audit_results": [], "claims": []}

    audit_results = []
    updated_claims = []
    structured_auditor = llm.with_structured_output(AuditRecord)

    for claim in claims:
        claim_copy = dict(claim)
        original_claim = claim_copy.get("original_claim", "")
        source_url = claim_copy.get("source_url", "")
        evidence_type = claim_copy.get("evidence_type", "search_snippet")
        
        print(f"\nAUDITOR -> fetching {source_url if source_url else '<No URL>'}")
        
        # Independently fetch the cited source page
        if source_url and source_url != "No URL":
            try:
                fetched_data = fetch_page.invoke({"url": source_url})
            except Exception as e:
                fetched_data = f"[FETCH STATUS: FAILED]\nURL: {source_url}\nREASON: Exception during fetch ({str(e)})"
        else:
            fetched_data = "[FETCH STATUS: FAILED]\nURL: None\nREASON: No source URL provided by Analyst."

        if fetched_data.startswith("[FETCH STATUS: FAILED]"):
            verdict = "unsupported"
            verification_status = "failed"
            reasoning = f"Independent page fetch failed for URL '{source_url}'. The original claim cannot be verified without accessible source page content."
            supporting_evidence = fetched_data

            print(f"AUDITOR -> fetch failed: {source_url if source_url else '<No URL>'}")
            print(f"AUDITOR -> verdict: UNSUPPORTED | evidence_type: {evidence_type} | verification_status: {verification_status}")
            
            record = {
                "original_claim": original_claim,
                "source_url": source_url,
                "evidence_type": evidence_type,
                "verification_status": verification_status,
                "verdict": verdict,
                "reasoning": reasoning,
                "supporting_evidence": supporting_evidence
            }
            audit_results.append(record)
            claim_copy["verification_status"] = verification_status
            updated_claims.append(claim_copy)
        else:
            print(f"AUDITOR -> fetch succeeded: {source_url}")
            audit_prompt = (
                f"You are an independent Auditor performing zero-trust verification of an ORIGINAL user claim.\n\n"
                f"ORIGINAL CLAIM TO EVALUATE:\n\"{original_claim}\"\n\n"
                f"CITED SOURCE URL:\n{source_url}\n\n"
                f"INDEPENDENTLY FETCHED SOURCE PAGE CONTENT:\n{fetched_data[:8000]}\n\n"
                f"STRICT RULES:\n"
                f"1. Base your verdict EXCLUSIVELY on the INDEPENDENTLY FETCHED SOURCE PAGE CONTENT provided above.\n"
                f"2. Do NOT evaluate based on search snippets, memory, or external assumptions.\n"
                f"3. Assign verdict='supported' ONLY if the fetched page content explicitly and directly verifies the original claim.\n"
                f"4. Assign verdict='contradicted' if the fetched page content directly refutes or contradicts the original claim (e.g. claim states 2010 but fetched page states 2014).\n"
                f"5. Assign verdict='unsupported' if the fetched page content does not contain sufficient facts to verify or disprove the original claim.\n"
                f"6. Do NOT invent or fabricate evidence. In supporting_evidence, extract direct quote excerpts directly from the fetched page content."
            )
            try:
                res = structured_auditor.invoke([HumanMessage(content=audit_prompt)])
                if res:
                    verdict = res.verdict
                    if verdict == "supported":
                        verification_status = "audited_supported"
                    elif verdict == "contradicted":
                        verification_status = "audited_contradicted"
                    else:
                        verification_status = "failed"
                    
                    print(f"AUDITOR -> verdict: {verdict.upper()} | evidence_type: {evidence_type} | verification_status: {verification_status}")
                    
                    record = res.model_dump()
                    record["evidence_type"] = evidence_type
                    record["verification_status"] = verification_status
                    audit_results.append(record)
                    claim_copy["verification_status"] = verification_status
                    updated_claims.append(claim_copy)
                else:
                    verdict = "unsupported"
                    verification_status = "failed"
                    print(f"AUDITOR -> verdict: UNSUPPORTED (Empty Response) | evidence_type: {evidence_type} | verification_status: {verification_status}")
                    record = {
                        "original_claim": original_claim,
                        "source_url": source_url,
                        "evidence_type": evidence_type,
                        "verification_status": verification_status,
                        "verdict": verdict,
                        "reasoning": "Auditor model returned empty evaluation response.",
                        "supporting_evidence": "None"
                    }
                    audit_results.append(record)
                    claim_copy["verification_status"] = verification_status
                    updated_claims.append(claim_copy)
            except Exception as e:
                verdict = "unsupported"
                verification_status = "failed"
                print(f"AUDITOR -> verdict: UNSUPPORTED (Exception) | evidence_type: {evidence_type} | verification_status: {verification_status}")
                record = {
                    "original_claim": original_claim,
                    "source_url": source_url,
                    "evidence_type": evidence_type,
                    "verification_status": verification_status,
                    "verdict": verdict,
                    "reasoning": f"Auditor evaluation error: {str(e)}",
                    "supporting_evidence": "None"
                }
                audit_results.append(record)
                claim_copy["verification_status"] = verification_status
                updated_claims.append(claim_copy)

    print("\n[VALIDATION DEBUG CHECK - EVIDENCE & AUDIT SUMMARY]:")
    for idx, (c, a) in enumerate(zip(updated_claims, audit_results), 1):
        print(f"  Record {idx}:")
        print(f"    - Source URL: {c.get('source_url', 'No URL')}")
        print(f"    - Evidence Type: {c.get('evidence_type', 'unknown')}")
        print(f"    - Verification Status: {c.get('verification_status', 'unknown')}")
        print(f"    - Auditor Verdict: {a.get('verdict', 'unknown').upper()}")

    return {"audit_results": audit_results, "claims": updated_claims}

def feedback(state: State):
    """Analyzes audit_results and claims to generate persistent research lessons that improve future Analyst runs."""
    audit_results = state.get("audit_results", [])
    claims = state.get("claims", [])

    print(f"\n[Feedback Engine Debug]: Received {len(audit_results)} audit result(s) and {len(claims)} claim(s).")
    
    if not audit_results and not claims:
        print("[Feedback Engine Debug Summary]: No audit results or claims received.")
        return {"feedback_lessons": []}

    combined_context = []
    for idx, audit in enumerate(audit_results):
        matching_claim = next((c for c in claims if c.get("original_claim") == audit.get("original_claim")), None)
        if not matching_claim and idx < len(claims):
            matching_claim = claims[idx]
            
        ctx = {
            "original_claim": audit.get("original_claim", ""),
            "auditor_verdict": audit.get("verdict", ""),
            "auditor_reasoning": audit.get("reasoning", ""),
            "supporting_evidence": audit.get("supporting_evidence", ""),
            "source_url": audit.get("source_url", ""),
            "evidence_type": matching_claim.get("evidence_type", "unknown") if matching_claim else "unknown",
            "verification_status": matching_claim.get("verification_status", "unknown") if matching_claim else "unknown",
            "analyst_evidence": matching_claim.get("evidence", "") if matching_claim else ""
        }
        combined_context.append(ctx)

    existing_lessons = load_memory()
    existing_lessons_text = "\n".join([f"- {l['lesson']}" for l in existing_lessons]) if existing_lessons else "None"

    structured_feedback = llm.with_structured_output(FeedbackOutput)
    
    prompt = (
        "You are the Research Feedback Engine. Analyze the following combined research claims and audit verification results:\n\n"
        f"AUDIT CONTEXT:\n{json.dumps(combined_context, indent=2)}\n\n"
        f"EXISTING LESSONS ALREADY IN MEMORY:\n{existing_lessons_text}\n\n"
        "YOUR TASK:\n"
        "Identify specific shortcomings, verification failures, or areas for improvement in the Analyst's research.\n"
        "Generate NEW concise, actionable research lessons for future Analyst runs.\n\n"
        "TRIGGER CRITERIA FOR GENERATING A LESSON:\n"
        "1. verdict == 'unsupported'\n"
        "2. verdict == 'contradicted'\n"
        "3. source fetch failed (e.g. 400 error, HTTP failure, unreachable URL)\n"
        "4. analyst relied on search_snippet instead of fetching the page (evidence_type == 'search_snippet')\n"
        "5. specific factual, numerical, percentage, or date assertions made without primary source verification\n\n"
        "RULES:\n"
        "- Do NOT generate exact duplicate lessons of those already listed under EXISTING LESSONS ALREADY IN MEMORY.\n"
        "- If an existing lesson is general (e.g. 'Prefer sources that can be independently fetched.'), but the current failure involves specific issues like search snippet reliance, unverified percentage assertions, or unverified claims, generate a NEW, distinct actionable lesson (e.g., 'Verify specific ownership percentages and financial claims against primary sources before asserting exact metrics.').\n"
        "- Set 'source_of_feedback' to 'auditor' for all lessons.\n"
        "- If all claims are fully supported and verified with fetched pages, return an empty list of lessons."
    )
    
    try:
        result = structured_feedback.invoke([HumanMessage(content=prompt)])
        new_lessons = [l.model_dump() for l in result.lessons] if result and result.lessons else []
    except Exception as e:
        print(f"FEEDBACK -> Exception during feedback generation: {e}")
        new_lessons = []

    added, duplicates = add_lessons_to_memory(new_lessons)
    
    print(f"\n[Feedback Engine Debug Summary]:")
    print(f"  - Audit results received: {len(audit_results)}")
    print(f"  - Claims received: {len(claims)}")
    print(f"  - Total lessons generated by LLM: {len(new_lessons)}")
    for idx, item in enumerate(new_lessons, 1):
        is_dup = any(item.get("lesson", "").strip().lower() == d.get("lesson", "").strip().lower() for d in duplicates)
        status_str = "DUPLICATE (ignored)" if is_dup else "NEW (added to memory)"
        print(f"    {idx}. [{status_str}] Lesson: {item.get('lesson', '')}")
        print(f"       Reason: {item.get('reason', '')}")
        print(f"       Source of Feedback: {item.get('source_of_feedback', 'auditor')}")

    return {"feedback_lessons": added}

def compose_final_answer(state: State):
    """Composes the user-facing final answer AFTER the Auditor and Feedback have run.

    Evidence labels are resolved strictly from structured state (claims[].verification_status,
    cross-referenced with audit_results for verdict/reasoning). The LLM is not used to decide
    or invent evidence labels - it only sees the already-resolved labels as fixed facts, so the
    pre-audit chatbot draft is never treated as the final answer.
    """
    claims = state.get("claims", [])
    audit_results = state.get("audit_results", [])

    # Index audit results by (original_claim, source_url) so each URL keeps its own,
    # independent status instead of being merged with other URLs for the same claim.
    audit_lookup = {}
    for audit in audit_results:
        key = (audit.get("original_claim", ""), audit.get("source_url", ""))
        audit_lookup[key] = audit

    print("\n[FINAL ANSWER DEBUG - PER-CLAIM EVIDENCE LABELS]:")

    sections = []
    for idx, claim in enumerate(claims, 1):
        original_claim = claim.get("original_claim", "")
        source_url = claim.get("source_url", "") or "N/A"
        evidence_type = claim.get("evidence_type", "unknown")
        verification_status = claim.get("verification_status", "unknown")

        audit = audit_lookup.get((claim.get("original_claim", ""), claim.get("source_url", "")))
        auditor_verdict = audit.get("verdict") if audit else None
        auditor_reasoning = audit.get("reasoning") if audit else None
        supporting_evidence = (audit.get("supporting_evidence") if audit else None) or claim.get("evidence", "")

        # Deterministic label resolution - NOT decided by the LLM.
        evidence_label = get_evidence_label(verification_status)

        print(f"  Claim {idx}:")
        print(f"    - original_claim: {original_claim}")
        print(f"    - source_url: {source_url}")
        print(f"    - evidence_type: {evidence_type}")
        print(f"    - verification_status: {verification_status}")
        print(f"    - auditor_verdict: {auditor_verdict}")
        print(f"    - final_evidence_label: {evidence_label}")

        lines = [
            f"**Claim {idx}:** {original_claim}",
            f"- Source URL: {source_url}",
            f"- Evidence status: **{evidence_label}**",
        ]
        if auditor_verdict:
            lines.append(f"- Auditor verdict: {auditor_verdict.upper()}")
        if auditor_reasoning:
            lines.append(f"- Auditor reasoning: {auditor_reasoning}")
        if supporting_evidence:
            lines.append(f"- Evidence excerpt: {supporting_evidence}")

        sections.append("\n".join(lines))

    if sections:
        final_text = "## Final Verified Answer\n\n" + "\n\n".join(sections)
    else:
        final_text = (
            "## Final Verified Answer\n\n"
            "No structured factual claims requiring evidence verification were extracted "
            "from this research session."
        )

    return {"messages": [AIMessage(content=final_text)]}

def route_after_chatbot(state: State):
    """Routes to tools node if tool call exists; otherwise routes to extract_claims node."""
    route = tools_condition(state)
    if route == END:
        return "extract_claims"
    return route

# 5. Build the Agent Graph
graph_builder = StateGraph(State)

# Add nodes
graph_builder.add_node("chatbot", chatbot)
tool_node = ToolNode(tools=tools)
graph_builder.add_node("tools", tool_node)
graph_builder.add_node("extract_claims", extract_claims)
graph_builder.add_node("auditor", auditor)
graph_builder.add_node("feedback", feedback)
graph_builder.add_node("compose_final_answer", compose_final_answer)

# Define edges
graph_builder.add_edge(START, "chatbot")

graph_builder.add_conditional_edges(
    "chatbot",
    route_after_chatbot,
    {"tools": "tools", "extract_claims": "extract_claims"}
)
graph_builder.add_edge("tools", "chatbot")
graph_builder.add_edge("extract_claims", "auditor")
graph_builder.add_edge("auditor", "feedback")
graph_builder.add_edge("feedback", "compose_final_answer")
graph_builder.add_edge("compose_final_answer", END)

# Compile graph
agent = graph_builder.compile()

if __name__ == "__main__":
    print("[Agent] Agent is ready! Ask me a research question. (Type 'quit' to exit)\n")
    while True:
        try:
            user_input = input("User: ")
            if user_input.lower() in ["quit", "exit", "q"]:
                break
            
            # Stream the graph execution
            events = agent.stream(
                {"messages": [HumanMessage(content=user_input)]},
                stream_mode="values"
            )
            
            for event in events:
                message = event["messages"][-1]

                # Print tool-call activity for transparency. The chatbot's pre-audit
                # draft content is intentionally NOT printed here - it has not yet been
                # verified by the Auditor, so it must not be shown as the answer.
                # The verified final answer is printed after the graph finishes running,
                # from the compose_final_answer node's output.
                if message.type == "ai":
                    if message.tool_calls:
                        print(f"[Agent calling tool: {message.tool_calls[0]['name']}]")
                # Print the Tool's output
                elif message.type == "tool":
                    print(f"[Tool Result: {message.content}]")

            # Print the verified final answer, composed after the Auditor and Feedback
            # have run (compose_final_answer is the last node before END).
            if event and event.get("messages") and event["messages"][-1].type == "ai":
                print(f"\nAgent (Verified Final Answer):\n{event['messages'][-1].content}\n")

            # Print extracted claims stored in graph state if present
            if "claims" in event and event["claims"]:
                print("\n[Structured Claims & Evidence in State]:")
                for idx, claim in enumerate(event["claims"], 1):
                    print(f"  Original Claim {idx}: {claim.get('original_claim', '')}")
                    print(f"    - Evidence: {claim.get('evidence', '')}")
                    print(f"    - Source: {claim.get('source_title', '')} ({claim.get('source_url', '')})")
                    print(f"    - Evidence Type: {claim.get('evidence_type', '')} | Verification Status: {claim.get('verification_status', '')}\n")
                    
            # Print auditor verification results stored in graph state if present
            if "audit_results" in event and event["audit_results"]:
                print("\n[Auditor Verification Results in State]:")
                for idx, audit in enumerate(event["audit_results"], 1):
                    print(f"  Audit {idx}: {audit.get('original_claim', '')}")
                    print(f"    - Source: {audit.get('source_url', '')}")
                    print(f"    - Verdict: {audit.get('verdict', '').upper()}")
                    print(f"    - Reasoning: {audit.get('reasoning', '')}")
                    print(f"    - Supporting Evidence: {audit.get('supporting_evidence', '')}\n")

            # Print feedback lessons stored in graph state if present
            if "feedback_lessons" in event and event["feedback_lessons"]:
                print("\n[Feedback Engine Lessons Saved to Memory]:")
                for idx, item in enumerate(event["feedback_lessons"], 1):
                    print(f"  Lesson {idx}: {item.get('lesson', '')}")
                    print(f"    - Reason: {item.get('reason', '')}")
                    print(f"    - Source of Feedback: {item.get('source_of_feedback', '')}\n")

        except Exception as e:
            print(f"Error: {e}")
            print("Did you set your API key in the .env file?")
            break



