"""Names the assistant can recognise, limited to what the asking user may see, and fuzzy matching."""

import re
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, Product, Role, Territory, User
from app.services.access import salesperson_scope

# Words too common to identify anything on their own (they still help break ties).
GENERIC = {
    "store", "stores", "general", "kirana", "traders", "trader", "mart", "wholesale", "enterprises",
    "distributors", "foods", "food", "restaurant", "caterers", "dhaba", "supermart", "hypermarket",
    "fresh", "provision", "new", "the", "and", "pvt", "ltd", "co", "rice", "oil", "powder", "dal",
    "pack", "family", "bar", "drink", "soap", "sales", "team", "shop", "india",
}
SALES_ROLES = {Role.SALES_EXECUTIVE, Role.SALES_MANAGER, Role.REGIONAL_MANAGER, Role.SALES_HEAD}


def normalise(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"(\d+)\s+(kg|g|gm|l|ml|ltr)\b", r"\1\2", text)  # "10 kg" -> "10kg"
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Entity:
    id: uuid.UUID
    name: str
    aliases: tuple[str, ...] = ()  # codes / SKUs

    @property
    def tokens(self) -> list[str]:
        return normalise(self.name).split()


@dataclass
class Catalog:
    customers: list[Entity] = field(default_factory=list)
    products: list[Entity] = field(default_factory=list)
    # Everyone in sales across the organisation, so "how is Rohan doing?" is *recognised* even when
    # the asker may not see Rohan's numbers; access is then refused explicitly, not silently ignored.
    people: list[Entity] = field(default_factory=list)
    territories: list[Entity] = field(default_factory=list)


def load_catalog(db: Session, user: User) -> Catalog:
    scope = salesperson_scope(db, user)
    org = user.organization_id

    cust = select(Customer.id, Customer.name, Customer.code).where(Customer.organization_id == org)
    if scope is not None:
        cust = cust.where(Customer.owner_id.in_(scope))
    people = select(User.id, User.full_name, User.role).where(User.organization_id == org, User.deleted_at.is_(None))

    return Catalog(
        customers=[Entity(i, n, (c,) if c else ()) for i, n, c in db.execute(cust)],
        products=[Entity(i, n, (s,)) for i, n, s in db.execute(select(Product.id, Product.name, Product.sku).where(Product.organization_id == org))],
        people=[Entity(i, n) for i, n, r in db.execute(people) if r in SALES_ROLES],
        territories=[Entity(i, n) for i, n in db.execute(select(Territory.id, Territory.name).where(Territory.organization_id == org))],
    )


def _token_hit(token: str, words: set[str]) -> bool:
    if token in words:
        return True
    # Tolerate small typos in longer words ("basmathi" -> "basmati").
    return len(token) >= 5 and any(len(w) >= 5 and SequenceMatcher(None, token, w).ratio() >= 0.85 for w in words)


@dataclass
class Match:
    entities: list[Entity]
    matched: set[str]
    score: int

    @property
    def ambiguous(self) -> bool:
        return len(self.entities) > 1


def find(text: str, entities: list[Entity]) -> Match | None:
    """Best-matching entities mentioned in `text`. Several equally good ones = ambiguous."""
    norm = f" {normalise(text)} "
    words = set(norm.split())
    best: list[tuple[int, Entity, set[str]]] = []
    for entity in entities:
        tokens = entity.tokens
        hit = {t for t in tokens if _token_hit(t, words)}
        distinctive = {t for t in hit if t not in GENERIC and not re.fullmatch(r"\d+\w*", t)}
        alias_hit = any(f" {normalise(a)} " in norm for a in entity.aliases if a)
        full = f" {' '.join(tokens)} " in norm
        if not (distinctive or alias_hit or full):
            continue
        score = len(hit) + (10 if full else 0) + (10 if alias_hit else 0)
        best.append((score, entity, hit))
    if not best:
        return None
    top = max(s for s, _, _ in best)
    winners = [(e, h) for s, e, h in best if s == top]
    return Match([e for e, _ in winners], set().union(*(h for _, h in winners)), top)


def find_by_name(name: str | None, entities: list[Entity]) -> Match | None:
    """Resolve a name the LLM extracted (or a canonical name) to entities."""
    if not name:
        return None
    exact = [e for e in entities if normalise(e.name) == normalise(name) or normalise(name) in map(normalise, e.aliases)]
    if exact:
        return Match(exact[:1], set(normalise(name).split()), 99)
    return find(name, entities)
