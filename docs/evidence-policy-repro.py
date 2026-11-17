"""Read-only reproductions for docs/evidence-policy-inconsistencies.md.

Builds in-memory records only; writes no data and runs no inference. Run from the repository root:
    ProjectWatchAI/.venv/Scripts/python.exe -I docs/evidence-policy-repro.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ProjectWatchAI"))
from contract_assessment import assess_contract
from project_review import contractor_performance
def photo(day, label=None, score=.9, detector="Roads", category="Roads", quality="validated"):
    return {"capturedAt": day, "url": "https://x/" + day + str(score) + str(label), "category": category, "scope": "",
            "yolo": [{"detector": detector, "status": "detected" if label else "no detection", "quality": quality,
                      "observation": f"YOLO detected {label} ({score:.0%} confidence).",
                      "detections": [{"label": label, "confidence": score}] if label else []}]}
def rec(photos, **kw):
    r = {"contractId": "T", "status": "Completed", "category": "Roads", "description": "Road", "dates": {"Reported completion": "2025-01-01"},
         "budgetPHP": {}, "galleryPhotoCount": len(photos), "photos": list(photos)}
    r.update(kw); return r
print("1a review_only road scope:", assess_contract(rec([photo("2025-01-02", "paved road surface", quality="review_only")]))["completion_evidence_category"])
print("1b review_only Roads w/o quality key (default provisional by name):", assess_contract(rec([{**photo("2025-01-02","paved road surface"), "yolo":[{k:v for k,v in photo("2025-01-02","paved road surface")["yolo"][0].items() if k!="quality"}]}]))["completion_evidence_category"])
print("1c review_only unfinished:", assess_contract(rec([photo("2025-01-02", "Under construction", .99, "Under construction", quality="review_only")]))["completion_evidence_category"])
w = photo("2025-01-02", "elevated water tank", .02, "Water Provision and Storage", "Water Provision and Storage", "review_only")
print("2a water tank 2% review_only:", assess_contract(rec([w], category="Water Provision and Storage"))["completion_evidence_category"])
s = photo("2025-01-02", "slope protection", .5, "Slope protection", quality="review_only")
print("2b slope 50% review_only:", assess_contract(rec([s], description="Road slope protection"))["completion_evidence_category"])
# 4 conflict
c = rec([photo("2025-01-02", "paved road surface", .6), photo("2025-01-02", "Under construction", .55, "Under construction")])
a = assess_contract(c); print("10a same-date scope60 vs work55:", a["completion_evidence_category"], "| work photos:", a["possible_unfinished_work_photos"], "| flags:", a["flags"])
c = rec([photo("2025-01-02", "paved road surface", .6), photo("2025-01-02", "Under construction", .9, "Under construction")])
a = assess_contract(c); print("10b same-date scope60 vs work90:", a["completion_evidence_category"], "| scope photos:", a["scope_supporting_photos"], "| flags:", a["flags"])
# 3 scoring
base = rec([photo("2025-02-15", "paved road surface")], contractor="X", dates={"Reported completion": "2025-01-01", "Contract expiry": "2025-01-01", "Reported start": "2024-06-01"})
base["assessment"] = assess_contract(base)
row = contractor_performance([base])[0]["contracts"][0]
print("3 scoring photo 45d after:", row["Level"], row["Schedule points"], row["Photo timing points"], row["Points"])
# 6
print("6a undated photos:", assess_contract(rec([photo("unknown", "paved road surface")]))["completion_evidence_category"])
print("6b gallery None no photos:", assess_contract(rec([], galleryPhotoCount=None))["completion_evidence_category"])
print("6c gallery 5 photos []:", assess_contract(rec([], galleryPhotoCount=5))["completion_evidence_category"])
print("6d not completed status:", assess_contract(rec([photo("2025-01-02")], status="On-Going"))["completion_evidence_category"])
print("6e status lowercase 'completed':", assess_contract(rec([photo("2025-01-02", "paved road surface")], status="completed"))["completion_evidence_category"])
# 11 tz
from contract_assessment import reported_day, photo_capture_order
print("11 reported_day naive datetime:", reported_day("2025-01-01T23:30:00"), "| Z:", reported_day("2025-01-01T23:30:00Z"))
# 13 budget negative/zero
r = rec([photo("2025-01-02","paved road surface")]); r["budgetPHP"]={"Award amount": 100, "Approved budget for the contract": 0}
print("13 budget ABC 0 flags:", assess_contract(r)["flags"])
# work with only scaffolding on completion day
print("x on-date work:", assess_contract(rec([photo("2025-01-01", "Under construction", .9, "Under construction")]))["completion_evidence_category"])
