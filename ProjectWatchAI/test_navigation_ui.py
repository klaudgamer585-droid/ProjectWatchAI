"""Phase 3 navigation and shared-system checks. Uses AppTest scripts and a disposable database only."""
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ui
from test_evidence_language import seed_review_db

HERE = Path(__file__).resolve().parent


def nav_script():
    """Three stub destinations rendered through the real ui.navigation()."""
    import streamlit as st
    import ui

    def explore():
        st.title("Explore stub")
        st.button("Go to admin", key="go_admin", on_click=ui.go_to, args=("Admin",),
                  kwargs={"admin_section": "Regional reviews"})

    def regional():
        st.title("Regional stub")

    def admin():
        st.title("Admin stub")
        st.text(f"section={st.session_state.get('admin_section')}")
        st.text_input("Draft", key="draft_note")
        # Equivalent of clicking the sidebar link: a page change that does not touch primary_navigation.
        if st.button("Native link to regional", key="native_regional"):
            st.switch_page(st.Page(regional, title="Regional review", url_path="regional-review"))

    if "draft_note" in st.session_state:  # same re-pin app.main() does for context keys
        st.session_state["draft_note"] = st.session_state["draft_note"]
    ui.navigation({"Explore projects": explore, "Regional review": regional, "Admin": admin}).run()


class NavigationTests(unittest.TestCase):
    def setUp(self):
        from streamlit.testing.v1 import AppTest
        self.at = AppTest.from_function(nav_script, default_timeout=30)

    def titles(self):
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        return [t.value for t in self.at.title]

    def test_default_destination_is_explore_and_state_reports_it(self):
        self.at.run()
        self.assertEqual(self.titles(), ["Explore stub"])
        self.assertEqual(self.at.session_state["primary_navigation"], "Explore projects")

    def test_exactly_three_destinations(self):
        self.assertEqual([title for title, *_ in ui.DESTINATIONS], ["Explore projects", "Regional review", "Admin"])
        self.assertEqual(len({path for _, path, *_ in ui.DESTINATIONS}), 3)

    def test_session_request_switches_destination(self):
        self.at.session_state["primary_navigation"] = "Regional review"
        self.at.run()
        self.assertEqual(self.titles(), ["Regional stub"])
        self.at.session_state["primary_navigation"] = "Admin"
        self.at.run()
        self.assertEqual(self.titles(), ["Admin stub"])

    def test_go_to_callback_navigates_and_presets_state(self):
        self.at.run()
        self.at.button(key="go_admin").click().run()
        self.assertEqual(self.titles(), ["Admin stub"])
        self.assertIn("section=Regional reviews", [t.value for t in self.at.text])
        self.assertEqual(self.at.session_state["primary_navigation"], "Admin")

    def test_native_page_switch_is_not_overridden_by_stale_state(self):
        self.at.session_state["primary_navigation"] = "Admin"
        self.at.run()
        self.at.text_input(key="draft_note").input("keep me").run()
        self.at.button(key="native_regional").click().run()
        self.assertEqual(self.titles(), ["Regional stub"])
        self.assertEqual(self.at.session_state["primary_navigation"], "Regional review")
        self.at.run()  # ordinary rerun stays put
        self.assertEqual(self.titles(), ["Regional stub"])
        self.at.session_state["primary_navigation"] = "Admin"  # back to Admin: the draft is still there
        self.at.run()
        self.assertEqual(self.at.text_input(key="draft_note").value, "keep me")

    def test_unknown_destination_is_rejected(self):
        with self.assertRaises(ValueError):
            ui.go_to("Settings")


class AppNavigationTests(unittest.TestCase):
    """The real app through native navigation, against a disposable database."""

    def setUp(self):
        from streamlit.testing.v1 import AppTest
        self.tmp = tempfile.TemporaryDirectory(dir=HERE)
        db = Path(self.tmp.name) / "fixture.db"
        seed_review_db(db)
        self.env = patch.dict(os.environ, {"PROJECTWATCH_PHOTO_REVIEW_DB": str(db)})
        self.env.start()
        self.at = AppTest.from_file(str(HERE / "app.py"), default_timeout=60)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_admin_and_regional_render_with_one_header_each(self):
        for destination, title in (("Admin", "Admin workspace"), ("Regional review", "Regional review")):
            self.at.session_state["primary_navigation"] = destination
            self.at.run()
            self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
            self.assertEqual([t.value for t in self.at.title], [title])
            self.assertEqual(self.at.session_state["primary_navigation"], destination)

    def test_admin_search_survives_a_trip_to_another_destination(self):
        self.at.session_state["primary_navigation"] = "Admin"
        self.at.run()
        self.at.text_input(key="saved_review_search").input("FIXTURE").run()
        self.at.session_state["primary_navigation"] = "Regional review"
        self.at.run()
        self.at.session_state["primary_navigation"] = "Admin"
        self.at.run()
        self.assertFalse(self.at.exception, [e.value for e in self.at.exception])
        self.assertEqual(self.at.text_input(key="saved_review_search").value, "FIXTURE")


class SharedSystemContractTests(unittest.TestCase):
    def test_css_avoids_testid_selectors_and_covers_focus_motion_and_current_page(self):
        css = ui._CSS
        self.assertNotIn("data-testid", css)
        for required in (":focus-visible", "prefers-reduced-motion: reduce", 'aria-current="page"',
                         "min-height:44px", "light-dark("):
            self.assertIn(required, css)
        self.assertNotRegex(css, r"#C9291B|#D93829[^;]*;\s*}\s*\.stApp a")  # active nav never brand red

    def test_brand_assets_are_derived_files_not_presentation_boards(self):
        brand = HERE / "static" / "brand"
        for name in ("mark-128.png", "favicon-64.png", "wordmark-ink.svg", "wordmark-ai.svg"):
            self.assertTrue((brand / name).is_file(), name)
        self.assertEqual(Path(ui.FAVICON).name, "favicon-64.png")
        source = (HERE / "ui.py").read_text(encoding="utf-8")
        for board in ("Logo System", "Icon Set", "Business Card"):
            self.assertNotIn(board, source)
        ai = (brand / "wordmark-ai.svg").read_text(encoding="utf-8")
        ink = (brand / "wordmark-ink.svg").read_text(encoding="utf-8")
        self.assertEqual(set(re.findall(r'fill="(#[0-9A-F]+)"', ai)), {"#D93829"})
        self.assertEqual(set(re.findall(r'fill="(#[0-9A-F]+)"', ink)), {"#1E1E1E"})


if __name__ == "__main__":
    unittest.main()
