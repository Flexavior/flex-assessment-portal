"""Grading orchestration — single entry for assess workflow."""
import json

from app.grader import extract_json
from app.llm import get_llm
from app.prompts import build_coverage_chunk_prompt, build_grading_prompt
from app.rag import retrieve_grading_context
from app.submission import SubmissionContent, SubmissionTooLargeError
from app.word_limits import evaluate_word_count


def _merge_coverage(chunk_results: list[dict], total_chunks: int, char_count: int) -> str:
    merged: dict[str, dict] = {}
    notes: list[str] = []
    quality_rank = {"mention": 1, "partial": 2, "full": 3}

    for chunk in chunk_results:
        notes.append(chunk.get("notes") or "")
        for item in chunk.get("topics_found") or []:
            topic = (item.get("topic") or "").strip()
            if not topic:
                continue
            key = topic.lower()
            existing = merged.get(key)
            item_quality = quality_rank.get(str(item.get("quality", "")).lower(), 0)
            if not existing:
                merged[key] = item
                continue
            existing_quality = quality_rank.get(str(existing.get("quality", "")).lower(), 0)
            if item_quality >= existing_quality:
                merged[key] = item

    lines = [
        f"Full submission scanned in {total_chunks} part(s); extracted {char_count:,} characters total.",
        f"Topics found across all parts: {len(merged)}",
    ]
    for item in merged.values():
        lines.append(
            "- {topic} ({quality}) — {section}: {evidence}".format(
                topic=item.get("topic", ""),
                quality=item.get("quality", "partial"),
                section=item.get("section_hint", "section not labelled"),
                evidence=item.get("evidence", ""),
            )
        )
    excerpt_notes = [n.strip() for n in notes if n and n.strip()]
    if excerpt_notes:
        lines.append("Per-part notes: " + " | ".join(excerpt_notes[:6]))
    return "\n".join(lines)


def _grade_single_pass(
    llm,
    index_instance,
    submission: SubmissionContent,
    assignment_text: str,
    rubric_text: str,
):
    student_text = submission.text
    context = retrieve_grading_context(
        index_instance,
        student_text,
        assignment_text,
        rubric_text,
    )
    prompt = build_grading_prompt(
        assignment_text=assignment_text,
        rubric_text=rubric_text,
        context=context,
        student_text=student_text,
        extraction_summary=submission.summary_for_prompt(),
        file_name=submission.source_file,
    )
    response = llm.complete(prompt)
    raw_text = (response.text if hasattr(response, "text") else str(response)).strip()
    return extract_json(raw_text), raw_text


def _grade_multi_pass(
    llm,
    index_instance,
    submission: SubmissionContent,
    assignment_text: str,
    rubric_text: str,
    chunks: list[str],
):
    chunk_results: list[dict] = []
    raw_parts: list[str] = []
    summary = submission.summary_for_prompt()

    for index, chunk in enumerate(chunks):
        prompt = build_coverage_chunk_prompt(
            assignment_text=assignment_text,
            chunk_text=chunk,
            chunk_index=index,
            total_chunks=len(chunks),
            extraction_summary=summary,
        )
        response = llm.complete(prompt)
        raw_text = (response.text if hasattr(response, "text") else str(response)).strip()
        raw_parts.append(raw_text[:500])
        chunk_results.append(extract_json(raw_text))

    coverage_digest = _merge_coverage(chunk_results, len(chunks), submission.char_count)
    context = retrieve_grading_context(
        index_instance,
        coverage_digest[:300],
        assignment_text,
        rubric_text,
    )
    prompt = build_grading_prompt(
        assignment_text=assignment_text,
        rubric_text=rubric_text,
        context=context,
        extraction_summary=summary,
        file_name=submission.source_file,
        coverage_digest=coverage_digest,
    )
    response = llm.complete(prompt)
    raw_text = (response.text if hasattr(response, "text") else str(response)).strip()
    result = extract_json(raw_text)
    result["grading_passes"] = len(chunks) + 1
    result["coverage_digest"] = coverage_digest
    return result, raw_text, "\n---\n".join(raw_parts)


def grade_submission(
    index_instance,
    submission: SubmissionContent,
    assignment_text: str = "",
    rubric_text: str = "",
    provider=None,
    model=None,
):
    raw_text = ""
    try:
        submission.validate_grading_size()
        chunks = submission.grading_chunks()
        llm = get_llm(provider=provider, model=model)

        if len(chunks) == 1:
            result, raw_text = _grade_single_pass(
                llm, index_instance, submission, assignment_text, rubric_text
            )
        else:
            result, raw_text, _ = _grade_multi_pass(
                llm, index_instance, submission, assignment_text, rubric_text, chunks
            )

        result["file_name"] = submission.source_file
        result["extraction_word_count"] = submission.word_count
        result["extraction_char_count"] = submission.char_count
        result["submission_word_count"] = submission.word_count
        result["word_count"] = evaluate_word_count(submission.word_count)
        result["grading_provider"] = provider
        result["grading_model"] = model
        result["grading_chunks"] = len(chunks)
        return result
    except SubmissionTooLargeError as exc:
        return {
            "error": str(exc),
            "file_name": submission.source_file,
            "extraction_word_count": submission.word_count,
            "extraction_char_count": submission.char_count,
        }
    except json.JSONDecodeError:
        return {
            "error": "LLM failed to return valid JSON",
            "raw_output": raw_text[:2000],
            "file_name": submission.source_file,
            "extraction_word_count": submission.word_count,
            "extraction_char_count": submission.char_count,
        }
    except Exception as exc:
        return {
            "error": f"Grading failed: {exc}",
            "raw_output": raw_text[:2000],
            "file_name": submission.source_file,
            "extraction_word_count": submission.word_count,
            "extraction_char_count": submission.char_count,
        }
