"""Pharma Brain seed entries — drug aliases, dosage forms, expiry rules, KPIs, interactions."""
from __future__ import annotations

# Drug aliases (generic ↔ brand). category='alias'.
DRUG_ALIASES: list[tuple[str, str]] = [
    ("Paracetamol", "Also known as Acetaminophen, Tylenol, Crocin, Calpol, Dolo"),
    ("Acetaminophen", "Same drug as Paracetamol"),
    ("Aspirin", "Also known as ASA, Acetylsalicylic Acid, Disprin, Ecosprin"),
    ("Amoxicillin", "Also known as Amox, Amoxil, Mox, Trimox"),
    ("Atorvastatin", "Brand: Lipitor, Atorlip, Storvas"),
    ("Metformin", "Brand: Glucophage, Glycomet, Fortamet"),
    ("Omeprazole", "Brand: Prilosec, Omez, Losec"),
    ("Lisinopril", "Brand: Prinivil, Zestril"),
    ("Salbutamol", "Same as Albuterol, brand: Ventolin, Asthalin"),
    ("Ibuprofen", "Brand: Advil, Motrin, Brufen, Combiflam"),
    ("Diclofenac", "Brand: Voltaren, Voveran, Cataflam"),
    ("Pantoprazole", "Brand: Protonix, Pantocid, Pan"),
    ("Clopidogrel", "Brand: Plavix, Clopilet, Deplatt"),
    ("Cetirizine", "Brand: Zyrtec, Cetzine, Alerid"),
    ("Loratadine", "Brand: Claritin, Lorfast"),
    ("Azithromycin", "Brand: Z-Pak, Zithromax, Azithral, Azee"),
    ("Ciprofloxacin", "Brand: Cipro, Ciplox, Cifran"),
    ("Levothyroxine", "Brand: Synthroid, Eltroxin, Thyronorm"),
    ("Amlodipine", "Brand: Norvasc, Amlong, Stamlo"),
    ("Losartan", "Brand: Cozaar, Losar, Repace"),
    ("Hydrochlorothiazide", "Abbreviated HCTZ, brand: Microzide"),
    ("Telmisartan", "Brand: Micardis, Telma, Telpres"),
    ("Rosuvastatin", "Brand: Crestor, Rosulip, Rozavel"),
    ("Pregabalin", "Brand: Lyrica, Pregaba, Pregalin"),
    ("Gabapentin", "Brand: Neurontin, Gabapin"),
    ("Sertraline", "Brand: Zoloft, Daxid, Serlift"),
    ("Fluoxetine", "Brand: Prozac, Flunil, Fludac"),
    ("Warfarin", "Brand: Coumadin, Warf, Uniwarfin"),
    ("Insulin Glargine", "Brand: Lantus, Basalog, Glaritus"),
    ("Esomeprazole", "Brand: Nexium, Esoz, Sompraz"),
    ("Furosemide", "Brand: Lasix, Frusemide, Frusenex"),
    ("Metoprolol", "Brand: Lopressor, Metolar, Betaloc"),
    ("Tramadol", "Brand: Ultram, Tramazac, Domadol"),
]

# Dosage forms. category='glossary'.
DOSAGE_FORMS: list[tuple[str, str]] = [
    ("Tablet", "Solid oral dose form, compressed powder, swallowed whole or chewed"),
    ("Capsule", "Gelatin shell containing powder or liquid, swallowed whole"),
    ("Syrup", "Sweetened liquid oral form, common for pediatric or cough preparations"),
    ("Injection", "Sterile parenteral form delivered IV/IM/SC, requires Rx and trained staff"),
    ("Drops", "Liquid for eye, ear, or nasal application, dose measured in drops"),
    ("Cream", "Semi-solid topical, oil-in-water emulsion, applied to skin"),
    ("Ointment", "Semi-solid topical, water-in-oil base, more occlusive than cream"),
    ("Inhaler", "Pressurized or dry-powder respiratory form, MDI or DPI device"),
    ("Patch", "Transdermal adhesive form for sustained absorption (nicotine, fentanyl)"),
    ("Suppository", "Solid form for rectal or vaginal insertion, melts at body temperature"),
]

# Expiry / shelf-life rules. category='pattern'.
EXPIRY_RULES: list[tuple[str, str]] = [
    ("Expiry < 30 days — pull from shelf", "Stock with under 30 days remaining should be pulled, marked-down, or returned to vendor before customer sale"),
    ("Expired stock cannot be sold", "Selling expired stock is an audit and legal violation; expired units must be quarantined and destroyed per regulator protocol"),
    ("FEFO ordering", "First-Expired-First-Out: dispense the batch with the earliest expiry first, regardless of receipt date"),
    ("Batch isolation on recall", "On manufacturer recall, every unit of the batch number must be quarantined within 24h and not dispensed"),
    ("Cold-chain expiry acceleration", "Refrigerated items (insulin, vaccines) exposed >2h above 8°C have shortened effective expiry and should be flagged"),
]

# Commercial KPIs / formulas. category='formula'.
COMMERCIAL_KPIS: list[tuple[str, str]] = [
    ("Stock Turnover Ratio", "Stock Turnover = COGS / Average Inventory. Higher = faster sell-through. Pharma target typically 6-12x annually"),
    ("GMROII", "Gross Margin Return on Inventory Investment = Gross Margin / Average Inventory Cost. Measures profit per dollar of inventory"),
    ("Stockout Rate", "Stockout Rate = Stockout Days / Total Days. Target < 2% on A-class SKUs, < 5% overall"),
    ("Slow-Mover Threshold", "Slow-mover = SKU with no movement in 60+ days. Triggers markdown, transfer, or return-to-vendor review"),
    ("DOH (Days On Hand)", "Days On Hand = Current Inventory / Daily Sales Velocity. Target 30-60 days; > 90 = overstock, < 14 = stockout risk"),
]

# Drug interactions. category='pattern' (alert-style).
DRUG_INTERACTIONS: list[tuple[str, str]] = [
    ("Warfarin + Aspirin interaction", "ALERT: Warfarin combined with Aspirin or NSAIDs increases bleeding risk significantly. Requires INR monitoring and prescriber confirmation"),
    ("ACE inhibitor + Potassium interaction", "ALERT: ACE inhibitors (Lisinopril, Enalapril) with potassium supplements or K-sparing diuretics → hyperkalemia risk"),
    ("SSRI + MAOI interaction", "ALERT: SSRIs (Sertraline, Fluoxetine) with MAOIs → serotonin syndrome risk. Requires 14-day washout between classes"),
    ("Statin + Macrolide interaction", "ALERT: Statins with Clarithromycin or Erythromycin → rhabdomyolysis risk via CYP3A4 inhibition"),
    ("Metformin + Iodinated contrast interaction", "ALERT: Hold Metformin 48h around iodinated contrast imaging due to lactic acidosis risk in renal impairment"),
    ("NSAID + Antihypertensive interaction", "ALERT: NSAIDs reduce efficacy of ACE inhibitors, ARBs, and diuretics, raising blood pressure"),
]


def all_entries() -> list[dict]:
    """Return every brain entry as a normalized dict for insertion."""
    out: list[dict] = []
    for name, val in DRUG_ALIASES:
        out.append({"category": "alias", "name": name, "value": val, "scope": "project"})
    for name, val in DOSAGE_FORMS:
        out.append({"category": "glossary", "name": name, "value": val, "scope": "project"})
    for name, val in EXPIRY_RULES:
        out.append({"category": "pattern", "name": name, "value": val, "scope": "project"})
    for name, val in COMMERCIAL_KPIS:
        out.append({"category": "formula", "name": name, "value": val, "scope": "project"})
    for name, val in DRUG_INTERACTIONS:
        out.append({"category": "pattern", "name": name, "value": val, "scope": "project"})
    return out
