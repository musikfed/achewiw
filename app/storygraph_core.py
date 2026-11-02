"""ACHEWIW canonical story graph: experimental domain core, not wired to v0.1 API.

This module deliberately does NOT call LLMs. Canon facts and events are authored
separately from presentation cards, reveals, and AI text proposals.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def new_id() -> str:
    return uuid.uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


CardKind = Literal["volume", "chapter", "scene", "paragraph"]
AIPolicy = Literal["off", "suggest", "draft", "rewrite"]
SegmentStatus = Literal["proposal", "accepted", "rejected"]


class StoryError(ValueError):
    """A narrative structural invariant was violated."""


class CanonFact(BaseModel):
    # Frozen: accidental in-place mutation cannot silently rewrite history.
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=new_id)
    statement: str = Field(min_length=1)
    category: str = "event"
    locked: bool = True
    supersedes_id: str | None = None


class WorldEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=new_id)
    world_order: int = Field(ge=0)
    summary: str = Field(min_length=1)
    fact_ids: list[str] = Field(default_factory=list)
    cause_event_ids: list[str] = Field(default_factory=list)
    location: str = ""
    actors: list[str] = Field(default_factory=list)


class Card(BaseModel):
    id: str = Field(default_factory=new_id)
    kind: CardKind
    title: str = ""
    parent_id: str | None = None
    position: int = 0
    deleted: bool = False
    ai_policy: AIPolicy = "suggest"
    author_locked: bool = False
    event_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    anchor: str = ""
    tempo: str = ""
    reader_info: str = ""
    hook: str = ""
    active_objects: list[str] = Field(default_factory=list)


class Reveal(BaseModel):
    id: str = Field(default_factory=new_id)
    fact_id: str
    scene_id: str
    mode: Literal["hint", "explicit", "misdirection", "silence"] = "hint"
    source: str = ""


class TextRevision(BaseModel):
    id: str = Field(default_factory=new_id)
    card_id: str
    text: str
    origin: Literal["human", "ai", "mixed"]
    status: SegmentStatus
    model_id: str | None = None
    replaces_revision_id: str | None = None
    created_at: str = Field(default_factory=now)
    accepted_at: str | None = None
    fact_ids: list[str] = Field(default_factory=list)


class StoryGraph(BaseModel):
    """Serializable work data. Persistence/permissions belong to the API layer."""

    id: str = Field(default_factory=new_id)
    title: str
    facts: dict[str, CanonFact] = Field(default_factory=dict)
    events: dict[str, WorldEvent] = Field(default_factory=dict)
    cards: dict[str, Card] = Field(default_factory=dict)
    reveals: dict[str, Reveal] = Field(default_factory=dict)
    revisions: dict[str, TextRevision] = Field(default_factory=dict)
    active_revisions: dict[str, str] = Field(default_factory=dict)

    def card(self, card_id: str) -> Card:
        if card_id not in self.cards:
            raise StoryError("Unknown card")
        return self.cards[card_id]

    def is_visible(self, card_id: str) -> bool:
        seen: set[str] = set()
        current = self.card(card_id)
        while True:
            if current.id in seen or current.deleted:
                return False
            seen.add(current.id)
            if current.parent_id is None:
                return True
            if current.parent_id not in self.cards:
                return False
            current = self.cards[current.parent_id]

    def children(self, parent_id: str | None) -> list[Card]:
        return sorted(
            (card for card in self.cards.values()
             if card.parent_id == parent_id and not card.deleted),
            key=lambda card: (card.position, card.id),
        )

    def add_card(self, kind: CardKind, title: str, parent_id: str | None = None) -> Card:
        parents = {"volume": None, "chapter": "volume",
                   "scene": "chapter", "paragraph": "scene"}
        if kind == "volume":
            if parent_id is not None:
                raise StoryError("Volumes belong to the work")
        else:
            if parent_id is None or self.card(parent_id).kind != parents[kind]:
                raise StoryError(f"Invalid parent for {kind}")
            if not self.is_visible(parent_id):
                raise StoryError("Parent is in the trash")
        position = len(self.children(parent_id))
        card = Card(kind=kind, title=title, parent_id=parent_id, position=position)
        self.cards[card.id] = card
        return card

    def move_card(self, card_id: str, parent_id: str | None, index: int) -> None:
        card = self.card(card_id)
        if not self.is_visible(card_id):
            raise StoryError("Cannot move a card in the trash")
        parents = {"volume": None, "chapter": "volume",
                   "scene": "chapter", "paragraph": "scene"}
        expected = parents[card.kind]
        if expected is None:
            if parent_id is not None:
                raise StoryError("Volume cannot have a parent")
        elif parent_id is None or self.card(parent_id).kind != expected:
            raise StoryError("Invalid destination")
        if parent_id is not None and not self.is_visible(parent_id):
            raise StoryError("Destination is in the trash")
        old_parent = card.parent_id
        destination = [s for s in self.children(parent_id) if s.id != card_id]
        index = max(0, min(index, len(destination)))
        destination.insert(index, card)
        card.parent_id = parent_id
        for position, sibling in enumerate(destination):
            sibling.position = position
        if old_parent != parent_id:
            for position, sibling in enumerate(self.children(old_parent)):
                sibling.position = position

    def delete_card(self, card_id: str) -> None:
        self.card(card_id).deleted = True

    def restore_card(self, card_id: str) -> None:
        card = self.card(card_id)
        if card.parent_id is not None and not self.is_visible(card.parent_id):
            raise StoryError("Restore the parent first")
        card.deleted = False

    def add_fact(self, statement: str, category: str = "event") -> CanonFact:
        fact = CanonFact(statement=statement, category=category)
        self.facts[fact.id] = fact
        return fact

    def amend_fact(self, old_fact_id: str, new_statement: str,
                   *, author_confirmed: bool = False) -> CanonFact:
        """Deliberate canon amendment, NEVER invoked by text generation."""
        if not author_confirmed:
            raise StoryError("Only an author-confirmed action can amend canon")
        if old_fact_id not in self.facts:
            raise StoryError("Unknown canon fact")
        fact = CanonFact(statement=new_statement,
                         category=self.facts[old_fact_id].category,
                         supersedes_id=old_fact_id)
        self.facts[fact.id] = fact
        # Existing references intentionally remain on the original fact until
        # the author reviews and relinks them.
        return fact

    def add_event(self, world_order: int, summary: str, *,
                  fact_ids: list[str] | None = None,
                  cause_event_ids: list[str] | None = None) -> WorldEvent:
        fact_ids = fact_ids or []
        cause_event_ids = cause_event_ids or []
        for fact_id in fact_ids:
            if fact_id not in self.facts:
                raise StoryError("Event references an unknown fact")
        for cause_id in cause_event_ids:
            if cause_id not in self.events:
                raise StoryError("Unknown cause event")
            if self.events[cause_id].world_order >= world_order:
                raise StoryError("A cause must precede its consequence")
        event = WorldEvent(world_order=world_order, summary=summary,
                           fact_ids=fact_ids, cause_event_ids=cause_event_ids)
        self.events[event.id] = event
        return event

    def attach_event(self, scene_id: str, event_id: str) -> None:
        card = self.card(scene_id)
        if card.kind != "scene" or not self.is_visible(scene_id):
            raise StoryError("An event can only be attached to an active scene")
        if event_id not in self.events:
            raise StoryError("Unknown event")
        if event_id not in card.event_ids:
            card.event_ids.append(event_id)

    def disclose(self, fact_id: str, scene_id: str, *,
                 mode: Literal["hint", "explicit", "misdirection", "silence"] = "hint") -> Reveal:
        if fact_id not in self.facts:
            raise StoryError("Reveal references an unknown fact")
        if self.card(scene_id).kind != "scene" or not self.is_visible(scene_id):
            raise StoryError("Reveals belong to visible scenes")
        reveal = Reveal(fact_id=fact_id, scene_id=scene_id, mode=mode)
        self.reveals[reveal.id] = reveal
        return reveal

    def _paragraph(self, card_id: str) -> Card:
        card = self.card(card_id)
        if card.kind != "paragraph" or not self.is_visible(card_id):
            raise StoryError("Text segments belong to visible paragraph cards")
        return card

    def write_human(self, card_id: str, text: str) -> TextRevision:
        self._paragraph(card_id)
        revision = TextRevision(
            card_id=card_id, text=text, origin="human", status="accepted",
            replaces_revision_id=self.active_revisions.get(card_id), accepted_at=now(),
        )
        self.revisions[revision.id] = revision
        self.active_revisions[card_id] = revision.id
        return revision

    def propose_ai(self, card_id: str, text: str, model_id: str,
                   fact_ids: list[str] | None = None) -> TextRevision:
        card = self._paragraph(card_id)
        if card.ai_policy == "off" or card.author_locked:
            raise StoryError("AI is disabled for this paragraph")
        fact_ids = fact_ids or []
        if any(fact_id not in self.facts for fact_id in fact_ids):
            raise StoryError("Proposal references unknown facts")
        revision = TextRevision(
            card_id=card_id, text=text, origin="ai", status="proposal",
            model_id=model_id, fact_ids=fact_ids,
        )
        self.revisions[revision.id] = revision
        return revision

    def accept_ai(self, revision_id: str) -> None:
        revision = self.revisions.get(revision_id)
        if revision is None or revision.status != "proposal" or revision.origin != "ai":
            raise StoryError("Not an open AI proposal")
        card = self._paragraph(revision.card_id)
        if card.author_locked or card.ai_policy == "off":
            raise StoryError("Paragraph is locked against AI")
        revision.replaces_revision_id = self.active_revisions.get(revision.card_id)
        revision.accepted_at = now()
        revision.status = "accepted"
        self.active_revisions[revision.card_id] = revision.id

    def reject_ai(self, revision_id: str) -> None:
        revision = self.revisions.get(revision_id)
        if revision is None or revision.status != "proposal":
            raise StoryError("Not an open proposal")
        revision.status = "rejected"

    def remove_active_text(self, card_id: str) -> None:
        self._paragraph(card_id)
        # The text is removed from the reading view, not destroyed in history.
        self.active_revisions.pop(card_id, None)

    def restore_revision(self, card_id: str, revision_id: str) -> None:
        self._paragraph(card_id)
        revision = self.revisions.get(revision_id)
        if (revision is None or revision.card_id != card_id
                or revision.status != "accepted"):
            raise StoryError("Revision is not an accepted version of this paragraph")
        self.active_revisions[card_id] = revision_id

    def timeline(self) -> list[WorldEvent]:
        return sorted(self.events.values(), key=lambda e: (e.world_order, e.id))

    def ordered_cards(self) -> list[Card]:
        result: list[Card] = []

        def walk(parent_id: str | None):
            for card in self.children(parent_id):
                if self.is_visible(card.id):
                    result.append(card)
                    walk(card.id)

        walk(None)
        return result

    def qa(self) -> list[str]:
        issues: list[str] = []
        for card in self.cards.values():
            if not self.is_visible(card.id):
                continue
            if card.kind == "scene" and len(card.active_objects) > 5:
                issues.append(f"Scene {card.id}: more than five active objects")
            if any(event_id not in self.events for event_id in card.event_ids):
                issues.append(f"Card {card.id}: unknown event reference")
            if any(fact_id not in self.facts for fact_id in card.fact_ids):
                issues.append(f"Card {card.id}: unknown fact reference")
        revealed = {r.fact_id for r in self.reveals.values()
                    if r.mode == "explicit" and self.is_visible(r.scene_id)}
        hinted = {r.fact_id for r in self.reveals.values()
                  if r.mode == "hint" and self.is_visible(r.scene_id)}
        for fact_id in hinted - revealed:
            issues.append(f"Hint {fact_id}: no explicit payoff yet")
        for event in self.events.values():
            if any(f not in self.facts for f in event.fact_ids):
                issues.append(f"Event {event.id}: unknown canon fact")
            if any(c not in self.events or self.events[c].world_order >= event.world_order
                   for c in event.cause_event_ids):
                issues.append(f"Event {event.id}: invalid cause")
        return issues
