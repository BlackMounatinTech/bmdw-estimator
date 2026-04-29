"""Pydantic schemas — the contract between LLM, calculator, and UI.

The 5-bucket cost model is the universal shape every job-type calculator
emits into. Labour, Materials, Equipment, Trucking, Spoil. Nothing else.

Type hints written for Python 3.9 compatibility.
"""

from datetime import date
from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# --- Catalogue items -------------------------------------------------------

class MaterialUnit(str, Enum):
    EACH = "each"
    LINEAR_FOOT = "lf"
    SQUARE_FOOT = "sf"
    CUBIC_YARD = "cu_yd"
    TON = "ton"
    BAG = "bag"


class MaterialCatalogueItem(BaseModel):
    sku: str
    name: str
    unit: MaterialUnit
    cost_per_unit: float
    notes: Optional[str] = None


class EquipmentCatalogueItem(BaseModel):
    sku: str
    name: str
    hourly_rate: Optional[float] = None
    daily_rate: Optional[float] = None
    weekly_rate: Optional[float] = None
    monthly_rate: Optional[float] = None
    weight_lb: Optional[float] = None  # informational; doesn't affect cost
    notes: Optional[str] = None


# --- Line items inside a quote --------------------------------------------

class CostBucket(str, Enum):
    LABOUR = "labour"
    MATERIALS = "materials"
    EQUIPMENT = "equipment"
    TRUCKING = "trucking"
    SPOIL = "spoil"


class LineItemEntry(BaseModel):
    """One row inside one of the 5 buckets for one job-type line item."""
    bucket: CostBucket
    description: str
    quantity: float
    unit: str
    unit_cost: float
    catalogue_sku: Optional[str] = None  # links back to materials/equipment catalogue
    rental_insurance_eligible: bool = True  # only counts when bucket == EQUIPMENT;
                                             # set False for trucks (tandem dump, etc.)

    @property
    def total_cost(self) -> float:
        return round(self.quantity * self.unit_cost, 2)


class JobLineItem(BaseModel):
    """One job-type within a quote (e.g. 'retaining wall', 'patio')."""
    job_type: Literal[
        "retaining_wall",
        "patio",
        "concrete_driveway",
        "gravel_driveway",
        "land_clearing",
        "foundation",
        "road_building",
        "drainage",
        "septic",
        "site_prep",
        "machine_hours",
    ]
    label: str
    inputs: Dict  # raw parameter dict the calculator received
    entries: List[LineItemEntry] = Field(default_factory=list)
    notes: Optional[str] = None
    project_notes: Optional[str] = None  # free-form notes about THIS project (site notes, reminders, etc.)
    attachments: List[str] = Field(default_factory=list)  # relative paths under data/attachments/<quote_id>/<idx>/

    def bucket_total(self, bucket: CostBucket) -> float:
        return round(sum(e.total_cost for e in self.entries if e.bucket == bucket), 2)

    @property
    def internal_cost(self) -> float:
        return round(sum(e.total_cost for e in self.entries), 2)


# --- Quote-level structures -----------------------------------------------

class LeadStatus(str, Enum):
    COLD = "cold"
    WARM = "warm"
    HOT = "hot"
    SOLD = "sold"
    LOST = "lost"


class Customer(BaseModel):
    name: str
    address: str
    email: Optional[str] = None
    phone: Optional[str] = None
    lead_status: LeadStatus = LeadStatus.COLD
    notes: Optional[str] = None  # free-form notes about the customer (preferences, payment habits, etc.)


class Markup(BaseModel):
    """Markup applied to convert internal cost to customer price.

    Single overall percentage by default. Per-bucket markups (e.g. higher %
    on materials than equipment) can be added later without changing callers.
    """
    overall_pct: float = 0.0
    per_bucket_pct: Optional[Dict[CostBucket, float]] = None


class ProjectPlanDay(BaseModel):
    day: int
    description: str


class QuoteStatus(str, Enum):
    DRAFT = "draft"
    SENT = "sent"
    WON = "won"
    LOST = "lost"


class Urgency(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class Quote(BaseModel):
    quote_id: str
    name: Optional[str] = None  # short project name (e.g. "Smith — wall + driveway")
    customer: Customer
    site_address: Optional[str] = None  # Defaults to customer.address if None
    urgency: Urgency = Urgency.MODERATE
    line_items: List[JobLineItem] = Field(default_factory=list)
    markup: Markup = Field(default_factory=Markup)
    rental_insurance_pct: float = 16.0  # auto-added on equipment subtotal as part of internal cost
    discount_pct: float = 0.0           # applied to subtotal after markup, before tax
    discount_flat: float = 0.0          # flat-dollar discount, additive with pct discount
    tax_pct: float = 12.0               # GST + PST flat, applied last
    project_plan: List[ProjectPlanDay] = Field(default_factory=list)
    start_date: Optional[date] = None
    status: QuoteStatus = QuoteStatus.DRAFT
    notes: Optional[str] = None
    quick_notes: Optional[str] = None
    contract_text: Optional[str] = None  # editable contract; if None, draft_contract_text() is used
    # Phase-state fields — captured on every autosave so you can close the app
    # mid-quote and resume exactly where you left off.
    quote_phase: int = 1                              # 1 = input, 2 = clarify, 3 = quote
    clarifying_questions: List[str] = Field(default_factory=list)
    clarifying_answers: str = ""
    review_questions: List[str] = Field(default_factory=list)
    review_answers: str = ""

    @property
    def effective_site_address(self) -> str:
        return self.site_address or self.customer.address

    # --- Pricing chain (cost → markup → discount → tax → customer total) ---

    @property
    def raw_entries_total(self) -> float:
        """Sum of every line item entry across all projects, before insurance."""
        return round(sum(li.internal_cost for li in self.line_items), 2)

    def bucket_total(self, bucket: CostBucket) -> float:
        """Sum of one bucket across all projects, raw (no insurance applied)."""
        return round(sum(li.bucket_total(bucket) for li in self.line_items), 2)

    @property
    def rental_insurance_subtotal(self) -> float:
        """Sum of equipment-bucket entries that are insurance-eligible.

        Excludes trucks (tandem dump, truck and pup, etc.) because they have
        their own commercial insurance, not rental insurance. Per-item
        eligibility flag lives on each LineItemEntry.
        """
        total = 0.0
        for li in self.line_items:
            for e in li.entries:
                if e.bucket == CostBucket.EQUIPMENT and e.rental_insurance_eligible:
                    total += e.total_cost
        return round(total, 2)

    @property
    def rental_insurance_amount(self) -> float:
        """16% (default) of the insurance-eligible equipment subtotal."""
        return round(self.rental_insurance_subtotal * self.rental_insurance_pct / 100, 2)

    @property
    def internal_cost(self) -> float:
        """Total cost to BMDW: every entry + rental insurance on equipment."""
        return round(self.raw_entries_total + self.rental_insurance_amount, 2)

    @property
    def markup_amount(self) -> float:
        return round(self.internal_cost * self.markup.overall_pct / 100, 2)

    @property
    def subtotal_pre_discount(self) -> float:
        """Internal cost + markup. Pre-discount, pre-tax."""
        return round(self.internal_cost + self.markup_amount, 2)

    @property
    def discount_amount(self) -> float:
        """Combined percentage + flat discount. Applied to subtotal_pre_discount."""
        pct = round(self.subtotal_pre_discount * self.discount_pct / 100, 2)
        return round(pct + self.discount_flat, 2)

    @property
    def subtotal(self) -> float:
        """Subtotal AFTER discount. Tax applies on this."""
        return round(self.subtotal_pre_discount - self.discount_amount, 2)

    @property
    def tax_amount(self) -> float:
        return round(self.subtotal * self.tax_pct / 100, 2)

    @property
    def customer_total(self) -> float:
        """The single all-in number the customer sees. Cost → markup → discount → tax."""
        return round(self.subtotal + self.tax_amount, 2)

    @property
    def margin_pct(self) -> float:
        """Gross margin on the post-discount subtotal (the actual revenue)."""
        sub = self.subtotal
        if sub == 0:
            return 0.0
        return round((sub - self.internal_cost) / sub * 100, 2)


# --- LLM I/O contracts ----------------------------------------------------

class LineItemBrief(BaseModel):
    job_type: str
    raw_dimensions: Dict  # e.g. {"length_ft": 30, "height_ft": 4}
    notes: Optional[str] = None
    confidence: Literal["high", "medium", "low"] = "medium"
    needs_confirmation: List[str] = Field(default_factory=list)


class ParsedJobBrief(BaseModel):
    """What the LLM emits after parsing a voice/text brief.

    LLM only structures input. It never produces a price or a quantity that
    appears on a customer-facing quote. Numbers here are dimensions and counts
    the user spoke, not estimates.
    """
    customer: Optional[Customer] = None
    line_item_briefs: List[LineItemBrief]


# --- Quick-notes parser contract -----------------------------------------

class ParsedLineEntry(BaseModel):
    """One AI-parsed entry destined for a 5-bucket cost line.

    The AI returns these. Code looks up cost_per_unit from the relevant
    catalogue using catalogue_key. The AI MUST NOT supply unit_cost — that
    field is filled by code from the catalogue.
    """
    bucket: CostBucket
    description: str
    quantity: float
    unit: str
    catalogue_key: Optional[str] = None  # references key in materials/equipment/trucking/labour
    catalogue_type: Optional[Literal["materials", "equipment", "trucking", "labour"]] = None
    needs_catalogue_add: bool = False    # True if AI mentioned an item not yet in any catalogue
    notes: Optional[str] = None


class ParsedProjectPlanDay(BaseModel):
    day: int
    description: str


class ParsedProject(BaseModel):
    """One project the AI detected inside a multi-project quote.

    Each project becomes a JobLineItem when hydrated. Multi-project quotes
    (e.g. retaining wall + concrete driveway) emit one ParsedProject per
    detected project type.
    """
    job_type: Literal[
        "retaining_wall", "patio", "concrete_driveway", "gravel_driveway",
        "land_clearing", "foundation", "road_building", "drainage",
        "septic", "site_prep", "machine_hours",
    ]
    label: str  # e.g. "Retaining Wall 30' × 4'"
    line_entries: List[ParsedLineEntry] = Field(default_factory=list)
    project_plan: List[ParsedProjectPlanDay] = Field(default_factory=list)


class ParsedCustomer(BaseModel):
    """Customer info extracted from the contractor's voice notes."""
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None  # job site address
    urgency: Optional[str] = None  # "low" | "moderate" | "high"


class ParsedNotesOutput(BaseModel):
    """Full structured response from the quick-notes LLM call."""
    summary: str  # one-sentence summary of the entire quote
    projects: List[ParsedProject] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    suggested_quote_label: Optional[str] = None
    parsed_customer: Optional[ParsedCustomer] = None
