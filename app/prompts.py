"""Grading prompt — semantic topic matching, not rigid Q1/Q22 labels."""
import json


def build_coverage_chunk_prompt(
    assignment_text: str,
    chunk_text: str,
    chunk_index: int,
    total_chunks: int,
    extraction_summary: str,
) -> str:
    return f"""
You are reviewing part {chunk_index + 1} of {total_chunks} from a long student submission.
Read ONLY this excerpt and list assignment topics the student addresses here.

ASSIGNMENT BRIEF (required topics):
{assignment_text or "See rubric/context in final grading pass."}

EXTRACTION SUMMARY:
{extraction_summary}

--- SUBMISSION EXCERPT ({chunk_index + 1}/{total_chunks}) ---
{chunk_text}

Return ONLY valid JSON:
{{
  "chunk_index": {chunk_index + 1},
  "topics_found": [
    {{
      "topic": "Topic from assignment brief",
      "section_hint": "Student heading or section where found",
      "quality": "full|partial|mention",
      "evidence": "Brief quote or paraphrase"
    }}
  ],
  "notes": "Anything notable in this excerpt"
}}
"""


def build_grading_prompt(
    assignment_text: str,
    rubric_text: str,
    context: str,
    student_text: str = "",
    extraction_summary: str = "",
    file_name: str = "",
    coverage_digest: str = "",
) -> str:
    if coverage_digest:
        submission_block = f"""--- FULL SUBMISSION COVERAGE DIGEST ---
The entire submission was read in multiple parts. Use this digest — do NOT claim topics are missing if listed here.

{coverage_digest}"""
    else:
        submission_block = f"""--- STUDENT SUBMISSION ---
{student_text}"""
    return f"""
You are an expert academic grader for a Computer Networking course.

SCORING AUTHORITY (in order):
1. Rubric — marks and bands
2. Assignment brief — required topics and tasks
3. Course material — factual baseline (slides first, textbook for detail)
4. Grading policy — how to use course sources

CRITICAL — HOW STUDENTS STRUCTURE ANSWERS:
- Students often use THEIR OWN headings (e.g. section 3.1, 3.2, chapter titles, topic names).
- They do NOT label answers as "Q1", "Q2". Never require exact Q-number labels in the submission.
- Map each assignment requirement to student content by TOPIC MEANING and semantic overlap.
- If a diagram or figure is embedded with explanatory text nearby, treat it as a partial or full answer for that topic.
- If the submission contains thousands of words, do NOT claim "no readable text".

EXTRACTION SUMMARY:
{extraction_summary}

--- REFERENCE MATERIAL ---
{context}

{submission_block}

--- GRADING INSTRUCTIONS ---
1. Read the assignment brief and rubric. List each required topic/task from the assignment.
2. For each topic, search the ENTIRE submission for matching content (any heading style, section number, or prose).
3. Mark covered=true if the student addressed the topic (fully, partially, or via diagram+caption).
4. Score using rubric bands (Developing / Proficient / Excellent). Use criteria from the rubric document.
5. Penalize factual errors vs course material. Note contradictions.
6. Estimate word count from the submission text provided (not zero if text is present).
7. Return professional, concise JSON only.

RETURN ONLY VALID JSON:
{{
  "student_name": "{file_name}",
  "submission_word_count_estimate": 0,
  "assignment_coverage": [
    {{
      "topic": "Brief topic from assignment (not necessarily Q1 label)",
      "covered": true,
      "coverage_note": "Where found in submission (heading/section) and quality"
    }}
  ],
  "criteria_scores": [
    {{
      "criterion": "From rubric",
      "score": 0,
      "max_score": 0,
      "justification": "..."
    }}
  ],
  "total_score_percent": 0.0,
  "total_score_out_of": 100,
  "strengths": ["..."],
  "areas_for_improvement": ["..."],
  "missing_or_partial": ["Topics weak or missing"],
  "contradictions": ["..."],
  "feedback": "Overall summary"
}}
"""
