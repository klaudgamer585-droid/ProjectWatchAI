"""Tests for photo grouping, flag links and photo citations. Pure logic; no storage."""
import unittest
from copy import deepcopy

from contract_assessment import PRE_COMPLETION_EQUIPMENT, assess_contract
from review_evidence import flag_links, photo_citations, photo_groups
from test_audit_regressions import photo, record


def assessed(photos, **changes):
    item = record(photos)
    item.update(changes)
    item["assessment"] = assess_contract(item)
    return item


def conflict_record(scope_score, work_score):
    return assessed([photo("2025-01-02", "paved road surface", scope_score),
                     photo("2025-01-02", "Under construction", work_score, "Under construction")])


class PhotoGroupTests(unittest.TestCase):
    def test_newest_first_then_earlier_then_unknown(self):
        photos = [photo("2025-01-02"), photo("2025-01-05"), {"capturedAt": None, "yolo": []}, photo("2025-01-05"),
                  photo("not a date")]
        groups = photo_groups(photos)
        self.assertEqual([g["role"] for g in groups], ["newest", "earlier", "unknown"])
        self.assertEqual([g["numbers"] for g in groups], [[2, 4], [1], [3, 5]])
        self.assertEqual(groups[0]["label"], "Newest dated group · 05 Jan 2025 · 2 photos")
        self.assertEqual(groups[1]["label"], "Earlier · 02 Jan 2025 · 1 photo")
        self.assertEqual(groups[2]["label"], "Date unknown · 2 photos")
        self.assertEqual((groups[0]["key"], groups[2]["key"], groups[2]["day"]), ("2025-01-05", None, None))

    def test_mixed_timezones_share_a_day(self):
        photos = [photo("2025-01-02T23:30:00Z"), photo("2025-01-03T07:30:00+08:00"), photo("2025-01-03")]
        groups = photo_groups(photos)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["numbers"], [1, 2, 3])

    def test_empty_and_agreement_with_assessment(self):
        self.assertEqual(photo_groups([]), [])
        item = assessed([photo("2025-01-02", "paved road surface"), photo("2025-01-09"), photo("2025-01-03")])
        newest = photo_groups(item["photos"])[0]["numbers"]
        self.assertEqual(newest, [item["assessment"]["latest_evidence_photo"]])

    def test_only_unknown_dates(self):
        groups = photo_groups([photo("nope")])
        self.assertEqual([g["role"] for g in groups], ["unknown"])


class ConflictLinkTests(unittest.TestCase):
    def check_both_ways(self, item, scope_photo, cue_photo):
        cites = photo_citations(item)
        counters = {n: [c["text"] for c in items if c["kind"] == "counter"] for n, items in cites.items()}
        self.assertTrue(any(f"photo {cue_photo}" in t for t in counters[scope_photo]), counters)
        self.assertTrue(any(f"photo {scope_photo}" in t for t in counters[cue_photo]), counters)

    def test_scope_kept_conflict_links_both_ways(self):
        item = conflict_record(.6, .55)
        self.assertEqual(item["assessment"]["possible_unfinished_work_photos"], [])
        self.check_both_ways(item, 1, 2)
        cites = photo_citations(item)
        self.assertIn("scope", [c["kind"] for c in cites[1]])

    def test_cues_kept_conflict_links_both_ways(self):
        item = conflict_record(.6, .9)
        self.assertEqual(item["assessment"]["possible_unfinished_work_photos"], [2])
        self.check_both_ways(item, 1, 2)
        self.assertIn("cue", [c["kind"] for c in photo_citations(item)[2]])
        links = {link["raw"]: link for link in flag_links(item)}
        link = links["Possible unfinished work in photos on or after reported completion"]
        self.assertEqual(link["photos"], [2])
        self.assertEqual(link["counter_photos"], [1])
        self.assertEqual(link["counter_note"], "Visible scope detections also appear in photo 1 on the same date.")

    def test_same_photo_in_both_sets(self):
        p = photo("2025-01-02", "paved road surface", .9)
        p["yolo"] += photo("2025-01-02", "Under construction", .5, "Under construction")["yolo"]
        cites = photo_citations(assessed([p]))
        texts = [c["text"] for c in cites[1] if c["kind"] == "counter"]
        self.assertEqual(len(texts), 2)
        self.assertTrue(all("this photo" in t for t in texts))


class FlagLinkTests(unittest.TestCase):
    def test_budget_flag_amounts(self):
        item = assessed([], budgetPHP={"Award amount": "1200000", "Approved budget for the contract": "1000000"})
        link = next(l for l in flag_links(item) if l["raw"].startswith("Award amount exceeds"))
        self.assertEqual(link["amounts"][0], ("Award amount", "₱1,200,000.00"))
        self.assertEqual(link["amounts"][1], ("Approved budget for the contract", "₱1,000,000.00"))
        self.assertIn("20.0%", link["amounts"][2][1])
        self.assertEqual((link["photos"], link["counter_photos"], link["dates"]), ([], [], []))
        self.assertTrue(link["discrepancy"])

    def test_budget_amount_unavailable(self):
        item = assessed([], budgetPHP={"Award amount": "1200000", "Approved budget for the contract": "1000000"})
        item["budgetPHP"] = {"Award amount": None, "Approved budget for the contract": "1000000"}
        item["assessment"]["flags"] = ["Award amount exceeds approved budget for the contract"]
        link = flag_links(item)[0]
        self.assertEqual(link["amounts"][0], ("Award amount", "Unavailable"))
        self.assertEqual(len(link["amounts"]), 2)

    def test_date_flag_dates(self):
        item = assessed([], dates={"Reported completion": "2024-01-01", "Reported start": "2024-03-05",
                                   "Contract expiry": "2023-12-01"})
        links = {l["raw"]: l for l in flag_links(item)}
        precedes = links["Reported completion precedes reported start"]
        self.assertEqual(precedes["dates"], [("Reported start", "05 Mar 2024"), ("Reported completion", "01 Jan 2024")])
        expiry = links["Contract expiry precedes reported start"]
        self.assertEqual(expiry["dates"][1], ("Contract expiry", "01 Dec 2023"))
        after = links["Reported completion is after contract expiry"]
        self.assertEqual([label for label, _ in after["dates"]], ["Reported completion", "Contract expiry"])

    def test_no_completion_date_flag(self):
        item = assessed([], dates={})
        link = next(l for l in flag_links(item) if l["raw"] == "Reported complete with no completion date")
        self.assertEqual(link["dates"], [("Reported completion", "Date unknown")])

    def test_progress_flag(self):
        item = assessed([], progress="80%")
        link = next(l for l in flag_links(item) if "physical progress below 100%" in l["raw"])
        self.assertEqual(link["amounts"][0], ("Reported progress", "80%"))
        self.assertEqual(link["amounts"][1], ("Reported status", "Completed"))

    def test_photos_precede_completion(self):
        item = assessed([photo("2024-12-01", "paved road surface"), photo("2024-12-05")])
        link = next(l for l in flag_links(item) if l["raw"] == "All dated sampled photos precede reported completion")
        self.assertEqual(link["photos"], [1, 2])
        self.assertEqual(link["dates"], [("Reported completion", "01 Jan 2025"), ("Latest photo", "05 Dec 2024")])

    def test_failed_flags_link_failed_photos(self):
        bad = photo("2025-01-02")
        bad["yolo"][0].update(status="unavailable", quality="unavailable")
        item = assessed([bad, photo("2025-01-02", "paved road surface")])
        links = {l["raw"]: l for l in flag_links(item)}
        partial = links["Some latest photo checks failed; retry incomplete analysis"]
        self.assertEqual(partial["photos"], [1])
        cites = photo_citations(item)
        self.assertIn("failed", [c["kind"] for c in cites[1]])
        self.assertNotIn("failed", [c["kind"] for c in cites[2]])

    def test_total_failure_flag(self):
        bad = photo("2025-01-02")
        bad["yolo"][0].update(status="unavailable", quality="unavailable")
        item = assessed([bad])
        link = next(l for l in flag_links(item) if l["raw"] == "Latest photo analysis unavailable; retry required")
        self.assertEqual(link["photos"], [1])

    def test_inconclusive_links_newest_group_only(self):
        item = assessed([photo("2025-01-02"), photo("2025-01-04"), photo("2025-01-04")])
        link = next(l for l in flag_links(item) if l["raw"].startswith("Latest photo evidence is inconclusive"))
        self.assertEqual(link["photos"], [2, 3])

    def test_scope_while_not_completed_has_cue_counter(self):
        item = assessed([photo("2025-01-02", "paved road surface", .9),
                         photo("2025-01-02", "Under construction", .5, "Under construction")], status="On-Going")
        link = next(l for l in flag_links(item) if l["raw"].startswith("Sampled photos meet"))
        self.assertEqual(link["photos"], [1])
        self.assertEqual(link["counter_photos"], [2])
        self.assertIn("Construction cues also appear in photo 2", link["counter_note"])
        self.assertEqual(link["amounts"], [("Reported status", "On-Going")])

    def test_pre_completion_equipment_flag_links_latest_photo(self):
        item = assessed([photo("2024-12-05")], category="Buildings and Facilities")
        item["assessment"]["flags"] = [PRE_COMPLETION_EQUIPMENT]
        item["assessment"]["latest_evidence_photo"] = 1
        link = flag_links(item)[0]
        self.assertEqual(link["photos"], [1])
        self.assertEqual(link["dates"], [("Reported completion", "01 Jan 2025")])

    def test_unknown_and_manual_flags_have_no_links(self):
        item = assessed([photo("2025-01-02", "paved road surface")])
        item["assessment"]["flags"] = ["Something new the rules emit", "Manual decision retained; underlying evidence "
                                       "changed or original evidence provenance is unavailable"]
        links = flag_links(item)
        self.assertEqual(len(links), 2)
        for link in links:
            self.assertEqual((link["photos"], link["counter_photos"], link["dates"], link["amounts"], link["counter_note"]),
                             ([], [], [], [], None))
        self.assertEqual(links[0]["text"], "Something new the rules emit")

    def test_flags_are_deduplicated_in_order(self):
        item = assessed([], dates={"Reported completion": "2024-01-01", "Reported start": "2024-03-05"})
        item["assessment"]["flags"] = ["Reported completion precedes reported start", "Zzz"]
        raws = [l["raw"] for l in flag_links(item)]
        self.assertEqual(raws[:2], ["Reported completion precedes reported start", "Zzz"])
        self.assertEqual(len(raws), len(set(raws)))


class RobustnessTests(unittest.TestCase):
    def test_records_without_assessment_keys(self):
        bare = {"contractId": "X", "photos": [{"capturedAt": "2025-01-02", "yolo": []}]}
        self.assertEqual(flag_links(bare), [])
        self.assertEqual(photo_citations(bare), {})
        self.assertEqual(flag_links({}), [])
        self.assertEqual(photo_citations({}), {})
        self.assertEqual(photo_groups(None), [])
        old = {"contractId": "X", "photos": [], "assessment": {"flags": ["Reported completion is after contract expiry"]}}
        link = flag_links(old)[0]
        self.assertEqual(link["dates"], [("Reported completion", "Date unknown"), ("Contract expiry", "Date unknown")])

    def test_out_of_range_photo_numbers_are_ignored(self):
        item = assessed([photo("2025-01-02", "paved road surface")])
        item["assessment"]["possible_unfinished_work_photos"] = [7]
        item["assessment"]["scope_supporting_photos"] = [0, 1]
        self.assertEqual(set(photo_citations(item)), {1})

    def test_inputs_are_not_mutated(self):
        items = [conflict_record(.6, .9), conflict_record(.6, .55),
                 assessed([photo("2024-12-01", "paved road surface")],
                          budgetPHP={"Award amount": "5", "Approved budget for the contract": "1"})]
        for item in items:
            before = deepcopy(item)
            photo_groups(item["photos"]), flag_links(item), photo_citations(item)
            self.assertEqual(item, before)


if __name__ == "__main__":
    unittest.main()
