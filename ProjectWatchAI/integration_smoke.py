"""Isolated Streamlit smoke checks; --live also exercises API, YOLO and Ollama.

Never writes production review records. Requires host networking for --live.
"""
import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


def main(live=False):
    import app
    from project_review import summarize_regional_evidence
    from streamlit.testing.v1 import AppTest

    report = {}
    with tempfile.TemporaryDirectory(prefix="integration-", dir=Path(__file__).parent) as tmp:
        database = Path(tmp) / "reviews.sqlite3"
        with patch.object(app, "PHOTO_REVIEW_DB", database), patch.dict(os.environ, {
            "PROJECTWATCH_PHOTO_REVIEW_DB": str(database), "TEMP": tmp, "TMP": tmp,
        }):
            # Render the complete dashboard without external requests or production records.
            response = ({"pagination": {"totalCount": 0, "totalPages": 0}, "data": []}, datetime.now(timezone.utc))
            with patch.object(app, "api_data", return_value=response):
                at = AppTest.from_string("import app\napp.main()", default_timeout=45).run()
                report["dashboard_exceptions"] = [e.message for e in at.exception]
                report["navigation"] = [
                    label.split(" ", 1)[1] if label.startswith(":material/") else label
                    for label in at.radio(key="primary_navigation").options
                ]
                assert not report["dashboard_exceptions"], report["dashboard_exceptions"]
                assert report["navigation"] == ["Explore projects", "Regional review", "Admin"]
                at.text_input(key="live_search").set_value("retained search").run()
                at.radio(key="primary_navigation").set_value("Admin").run()
                assert not at.exception, [e.message for e in at.exception]
                report["admin_sections"] = list(at.radio(key="admin_section").options)
                assert report["admin_sections"] == [
                    "Reviewed contracts", "Regional reviews", "Contract reviews", "Rerun reviews", "Model status",
                ]
                for section in report["admin_sections"]:
                    at.radio(key="admin_section").set_value(section).run()
                    assert not at.exception, [e.message for e in at.exception]
                at.radio(key="primary_navigation").set_value("Regional review").run()
                assert not at.exception, [e.message for e in at.exception]
                at.radio(key="primary_navigation").set_value("Explore projects").run()
                assert at.text_input(key="live_search").value == "retained search"
                assert not at.exception, [e.message for e in at.exception]
            tester = AppTest.from_file(str(Path(__file__).with_name("yolo_test_app.py")), default_timeout=45).run()
            report["tester_exceptions"] = [e.message for e in tester.exception]
            assert not report["tester_exceptions"], report["tester_exceptions"]
            print("Dashboard and tester render passed", flush=True)
            if live:
                records = []
                for contract in ("22AE0064", "23CB0047", "24CK0181"):
                    record = app.summarize_contract(contract, save=False)
                    assert record["galleryPhotoCount"] is not None, f"Gallery failed: {contract}"
                    records.append(record)
                    print(contract, record["assessment"]["completion_evidence_category"], flush=True)
                report["contracts"] = [{"contract": r["contractId"], "photos": len(r["photos"]),
                                        "assessment": r["assessment"],
                                        "detector_statuses": [{"detector": f["detector"], "status": f["status"], "quality": f["quality"]}
                                                              for p in r["photos"] for f in p["yolo"]]}
                                       for r in records]
                report["ollama"] = summarize_regional_evidence(records, app.review_model())
                assert not report["ollama"]["errors"], report["ollama"]["errors"]
                assert report["ollama"]["completed_batches"] == report["ollama"]["total_batches"]
                # Render the saved-review workspace, not only its empty state.
                app.save_contract_assessments(records, require_saved=True)
                workspace = AppTest.from_string("import app\napp.show_reviewed_contracts()", default_timeout=45).run()
                report["review_workspace_exceptions"] = [e.message for e in workspace.exception]
                assert not report["review_workspace_exceptions"], report["review_workspace_exceptions"]
                print("Live API, YOLO, Ollama and saved review workspace passed", flush=True)
    output = Path(__file__).parent / "data/integration-smoke.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    main(parser.parse_args().live)
