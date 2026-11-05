"""Deterministic anti-loop QA for Ollama long-form Russian prose.

Never edit, truncate, accept or replace the author's stored chapters.
Reject generated chunks BEFORE persisting, report old corrupt chapters for
editorial review, and keep contaminated RAG excerpts out of new prompts.
"""
from __future__ import annotations

from collections import Counter
import re

TOKEN = re.compile(r"[^\W_]+(?:[-’'][^\W_]+)*", re.UNICODE)

class ProseDegeneration(ValueError):
    """The model entered a repeating phrase/paragraph loop."""


def _tokens(value: str):
    matches=list(TOKEN.finditer(value))
    return [m.group().casefold() for m in matches],matches


def first_cycle(text: str) -> dict | None:
    """A repeated block of 3-18 words, six times or at least 30 words.

    Identify it inside a *single* Ollama response, not just against previous
    chapters. The 3-token minimum allows legitimate short rhetorical repeats.
    """
    tokens,matches=_tokens(text)
    if len(tokens)<30:return None
    # A single repeated word (e.g. «шёпотом» for two printed pages) was
    # invisible to the former 3-token detector. Require an extreme run so
    # ordinary literary emphasis is still permitted.
    run_start = 0
    for index in range(1, len(tokens)+1):
        if index<len(tokens) and tokens[index]==tokens[run_start]:
            continue
        run_length=index-run_start
        if run_length>=30:
            return {"kind":"word_loop","position":matches[run_start].start(),
                    "end_position":matches[index-1].end(),
                    "repeats":run_length,"sample":tokens[run_start]}
        run_start=index
    # Avoid quadratic scans over an entire already-written 20k-word book.
    for i in range(len(tokens)-17):
        for size in range(3,min(19,(len(tokens)-i)//5+1)):
            unit=tokens[i:i+size]
            if len(set(unit))<2:continue
            if tokens[i+size:i+size*2]!=unit:continue
            count=2
            while i+(count+1)*size<=len(tokens) and tokens[i+count*size:i+(count+1)*size]==unit:
                count+=1
            if count>=max(5,(30+size-1)//size) and count*size>=30:
                return {
                    "kind":"phrase_loop","position":matches[i].start(),
                    "end_position":matches[i+count*size-1].end(),
                    "repeats":count,"sample":" ".join(unit)[:140]
                }
    return None


def quality_findings(text: str, *, previous_chapters: str = "", limit: int = 5) -> list[dict]:
    """Strong lexical anomalies only, not a pretend semantic editor."""
    if not text.strip():return []
    tokens,_=_tokens(text)
    findings=[]
    cycle=first_cycle(text)
    if cycle:
        findings.append({
            "kind":cycle["kind"],"position":cycle["position"],
            "detail":f"Фраза «{cycle['sample']}» повторена подряд {cycle['repeats']} раз"
        })
    if len(tokens)>=70:
        # Repeated long n-grams across dispersed paragraphs indicate filler,
        # even if no adjacent cycle exists. Exact wording, conservative bar.
        gram=8
        frequencies=Counter(tuple(tokens[i:i+gram])
                            for i in range(len(tokens)-gram+1))
        common,n=max(frequencies.items(),key=lambda item:item[1])
        if n>=max(8,len(tokens)//120) and n*gram>=len(tokens)*0.055:
            findings.append({
                "kind":"reused_phrasing",
                "detail":f"Одинаковая последовательность из 8 слов встречается {n} раз: "
                         f"«{' '.join(common)[:125]}»"
            })
    if previous_chapters and len(tokens)>35:
        past,_=_tokens(previous_chapters)
        # Catch an entire opening/scene copied from an earlier chapter.
        # Ignore ordinary character names and brief repeated catchphrases.
        gram=20
        if len(past)>=gram and len(tokens)>=gram:
            past_grams={tuple(past[i:i+gram]) for i in range(len(past)-gram+1)}
            seen=set()
            for i in range(len(tokens)-gram+1):
                match=tuple(tokens[i:i+gram])
                if match in past_grams and match not in seen:
                    seen.add(match)
                    findings.append({
                        "kind":"previous_chapter_echo",
                        "detail":"Текст повторяет не менее 20 слов из ранее написанной главы: "
                                 f"«{' '.join(match)[:130]}»"
                    })
                    break
    return findings[:limit]


def check_quality(text: str, *, previous_chapters: str = "") -> None:
    findings=quality_findings(text,previous_chapters=previous_chapters,limit=1)
    if findings:
        raise ProseDegeneration("Повтор внутри ответа ИИ: "+findings[0]["detail"])


def prompt_tail(text: str, max_chars: int = 2200) -> str:
    """Exclude pathological loop from the MODEL prompt, not from the database."""
    if not text:return ""
    tail=text[-max_chars:]
    issue=first_cycle(tail)
    if not issue:return tail
    # Keep a NEW event after the repetition. Prior implementation cut the
    # whole suffix and hid the genuine last saved sentence from Qwen.
    before=tail[:issue["position"]].strip()
    after=tail[issue["end_position"]:].strip()
    if len(before)<100:
        absolute=len(text)-len(tail)+issue["position"]
        before=text[:absolute].rstrip()[-max_chars:]
    if len(before)>200:
        endings=list(re.finditer(r"[.!?…][»”']?(?=\s|$)",before))
        if endings:before=before[:endings[-1].end()]
    if after:
        clean=(before[-max_chars//2:]+"\n[Повторяющийся фрагмент пропущен из контекста.]\n"+after).strip()
    else:
        clean=before.rstrip()+"\n[Повторяющийся хвост пропущен из контекста.]"
    return clean[-max_chars:]


def streaming_loop(text: str) -> bool:
    """Cheap guard on the last 1700 characters of the unfinished model output."""
    return first_cycle(text[-1700:]) is not None
