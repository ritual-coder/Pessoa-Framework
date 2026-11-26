"""Tests for the parts that guard untrusted input and data on disk.

Run from the repo root:  python -m unittest discover tests
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "core"), os.path.join(ROOT, "scripts")]
# Keep the server's log of test runs out of the user's pessoa_bridge.log.
os.environ.setdefault("PESSOA_LOG_FILE", os.path.join(tempfile.gettempdir(), "pessoa_test_bridge.log"))

from converter import _self_test  # noqa: E402
from create_heteronym import create_heteronym  # noqa: E402
from activation import _band, _behaviour, build_activation_prompt  # noqa: E402
from hydrate import _unfence, hydrate_files, parse_hydration_blob  # noqa: E402
from naming import derive_heteronym_name, safe_character_dir, sanitize_heteronym_name  # noqa: E402

PROFILE = {"O": 3, "C": 3, "E": 3, "A": 3, "N": 3}


class NamingTest(unittest.TestCase):
    def test_sanitize_strips_path_parts(self):
        self.assertEqual(sanitize_heteronym_name("../../etc"), "etc")
        self.assertEqual(sanitize_heteronym_name(".."), "New_Heteronym")
        self.assertEqual(sanitize_heteronym_name("**Stack And Dagger**"), "Stack_And_Dagger")

    def test_derive_drops_parenthetical_note(self):
        skin = '- **Name**: Mara Quell (Otto says "Meesus Kwell")\n'
        self.assertEqual(derive_heteronym_name(skin), "Mara_Quell")

    def test_safe_character_dir_refuses_escapes(self):
        with tempfile.TemporaryDirectory() as base:
            outside = tempfile.mkdtemp()
            try:
                os.symlink(outside, os.path.join(base, "link"))
                for bad in ("../x", "/etc", "", ".", "link"):
                    with self.assertRaises(ValueError, msg=bad):
                        safe_character_dir(base, bad)
                self.assertEqual(
                    safe_character_dir(base, "ok"),
                    os.path.join(os.path.realpath(base), "ok"),
                )
            finally:
                shutil.rmtree(outside)


class ParserTest(unittest.TestCase):
    BLOB = "--- FILE: skin.md ---\n# Skin\nhello\n--- FILE: engine.md ---\nbody\n"

    def test_round_trip(self):
        self.assertEqual(
            parse_hydration_blob(self.BLOB),
            {"skin.md": "# Skin\nhello", "engine.md": "body"},
        )

    def test_windows_line_endings(self):
        crlf = self.BLOB.replace("\n", "\r\n")
        self.assertEqual(parse_hydration_blob(crlf), parse_hydration_blob(self.BLOB))


class CreateTest(unittest.TestCase):
    def setUp(self):
        # characters/* is gitignored, so this folder never reaches a commit.
        self.name = f"Test_Existing_{os.getpid()}"
        self.dir = os.path.join(ROOT, "characters", self.name)
        os.makedirs(self.dir)
        with open(os.path.join(self.dir, "seed.md"), "w") as f:
            f.write("original")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_refuses_existing_character(self):
        with self.assertRaises(FileExistsError):
            create_heteronym(self.name, "# engine", PROFILE, skin_content="# skin")
        self.assertEqual(os.listdir(self.dir), ["seed.md"])
        with open(os.path.join(self.dir, "seed.md")) as f:
            self.assertEqual(f.read(), "original")


class FailedWriteTest(unittest.TestCase):
    def test_failed_write_leaves_no_folder(self):
        name = f"Test_Failed_{os.getpid()}"
        path = os.path.join(ROOT, "characters", name)
        self.addCleanup(shutil.rmtree, path, True)
        with self.assertRaises(TypeError):  # None cannot be written: fails after makedirs
            create_heteronym(name, None, PROFILE)
        self.assertFalse(os.path.exists(path))


class UnfenceTest(unittest.TestCase):
    def test_fenced_json(self):
        self.assertEqual(_unfence('```json\n{"O": 3}\n```'), '{"O": 3}')
        self.assertEqual(_unfence('{"O": 3}'), '{"O": 3}')
        self.assertEqual(_unfence('```json\n{"O": 3}'), '{"O": 3}')  # unclosed


class BandTest(unittest.TestCase):
    def test_moderate_boundary_reads_as_balanced(self):
        self.assertEqual(_band(3.4), "Moderate")
        self.assertTrue(_behaviour("O", 3.4).startswith("balanced"))


class GetTemplateTest(unittest.TestCase):
    def test_reads_templates_and_blueprints_only(self):
        from base_server import get_template
        self.assertIn('"scale"', get_template("big_five.json"))
        self.assertIn("Coder", get_template("coder_blueprint.md"))
        for bad in ("../README.md", "/etc/passwd", "..", ""):
            self.assertTrue(get_template(bad).startswith("Error"), bad)


class ActivationRulesTest(unittest.TestCase):
    def test_rule_bodies_survive_any_format(self):
        rules = "# Title\n\n1. Integrity\n   - Never invent hashes.\n"
        out = build_activation_prompt("X", rules=rules)
        self.assertIn("Never invent hashes.", out)
        self.assertNotIn("# Title", out)


class HydrationReportTest(unittest.TestCase):
    def test_warns_about_missing_and_ignored_files(self):
        name = f"Test_Report_{os.getpid()}"
        self.addCleanup(shutil.rmtree, os.path.join(ROOT, "characters", name), True)
        files = {"engine.md": "e", "big_five.json": '{"O": 3}', "ai_cabinet.yaml": "x"}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            hydrate_files(files, name)
        report = out.getvalue()
        self.assertIn("missing layers: skin.md, seed.md, operational_rules.md", report)
        self.assertIn("ignored files: ai_cabinet.yaml", report)
        self.assertIn("no score found for C, E, A, N", report)


class DigestTest(unittest.TestCase):
    def test_module_self_test(self):
        from digest import _self_test as digest_self_test
        self.assertTrue(digest_self_test())

    def test_save_and_fetch_round_trip(self):
        import json
        import base_server
        with tempfile.TemporaryDirectory() as notes:
            with open(os.path.join(notes, "book.md"), "w", encoding="utf-8") as f:
                f.write("He counted nineteen red lights on the tower.")
            os.environ["PESSOA_ANALYSIS_DIR"] = notes
            self.addCleanup(os.environ.pop, "PESSOA_ANALYSIS_DIR", None)
            digest = json.dumps({"subject": "Mara Quell", "edges": [], "nodes": [
                {"id": "mara", "evidence": [{"quote": "nineteen red lights"},
                                             {"quote": "made up"}]}]})
            report = base_server.save_prima_digest("```json\n" + digest)
            self.assertIn("quotes verified against the source: 1/2", report)
            self.assertIn("'made up'", report)
            self.assertTrue(base_server.save_prima_digest(digest).startswith("ERROR"))
            self.assertIn("nineteen red lights", base_server.fetch_prima_digest())
            self.assertNotIn("digest.json", base_server.fetch_analysis_data())


def _text_pdf(text):
    """Smallest valid one-page PDF showing `text` (ASCII only)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


class SourcesTest(unittest.TestCase):
    def test_every_format_and_failure_mode(self):
        import zipfile
        from pypdf import PdfWriter
        import base_server
        with tempfile.TemporaryDirectory() as notes:
            j = lambda n: os.path.join(notes, n)
            with open(j("a.txt"), "w", encoding="utf-8") as f:
                f.write("Plain text about the tower.")
            with zipfile.ZipFile(j("b.docx"), "w") as z:
                z.writestr("word/document.xml",
                           '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                           'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Word</w:t>'
                           '</w:r><w:r><w:t xml:space="preserve"> document text</w:t></w:r>'
                           '</w:p></w:body></w:document>')
            with open(j("c.pdf"), "wb") as f:
                f.write(_text_pdf("Nineteen red lights"))
            blank = PdfWriter()
            blank.add_blank_page(612, 792)
            blank.write(j("d_scan.pdf"))
            with open(j("e.txt"), "wb") as f:
                f.write("café".encode("cp1252"))
            open(j("f.epub"), "w").close()

            os.environ["PESSOA_ANALYSIS_DIR"] = notes
            self.addCleanup(os.environ.pop, "PESSOA_ANALYSIS_DIR", None)
            out = base_server.fetch_analysis_data()
            for expected in ("Plain text about the tower.", "Word document text",
                             "Nineteen red lights", "d_scan.pdf: PDF has no extractable text",
                             "e.txt: not UTF-8", "Skipped (unsupported type"):
                self.assertIn(expected, out)
            self.assertIn("f.epub", out.split("Skipped")[1])


class ExtractTest(unittest.TestCase):
    def test_module_self_test(self):
        from extract import _self_test as extract_self_test
        self.assertTrue(extract_self_test())

    def test_tool_saves_verified_auto_digest(self):
        import base_server
        with tempfile.TemporaryDirectory() as notes:
            with open(os.path.join(notes, "book.txt"), "w", encoding="utf-8") as f:
                f.write("Mara Quell watched Gull Point from his window at dusk.\n\n"
                        "Later Mara met Ben on the stairs of the old building.\n\n"
                        "Mara told Ben about Gull Point and the strange figure.\n\n"
                        "Ben laughed, but Mara kept thinking about Gull Point.\n")
            os.environ["PESSOA_ANALYSIS_DIR"] = notes
            self.addCleanup(os.environ.pop, "PESSOA_ANALYSIS_DIR", None)
            report = base_server.extract_prima_graph("Mara Quell")
            self.assertIn("Mara_Quell.auto.digest.json", report)
            verified, total = report.split("source: ")[1].split("/")
            self.assertEqual(verified, total.split()[0])  # every quote verbatim
            one = base_server.fetch_prima_digest("Mara_Quell.auto.digest.json")
            self.assertIn("Gull Point", one)
            self.assertTrue(base_server.fetch_prima_digest("../book.txt").startswith("ERROR"))
            self.assertTrue(base_server.extract_prima_graph("Nobody Here").startswith("ERROR"))


class StudioTest(unittest.TestCase):
    def test_module_self_test(self):
        from studio import _self_test as studio_self_test
        self.assertTrue(studio_self_test())


class NetworkTest(unittest.TestCase):
    def test_module_self_test(self):
        from network import _self_test as network_self_test
        self.assertTrue(network_self_test())

    def test_build_and_query_tools(self):
        import base_server
        with tempfile.TemporaryDirectory() as notes:
            with open(os.path.join(notes, "poem.txt"), "w", encoding="utf-8") as f:
                f.write("the tower watched Mara\nMara loved Ilse\nIlse and the bed\n")
            os.environ["PESSOA_ANALYSIS_DIR"] = notes
            self.addCleanup(os.environ.pop, "PESSOA_ANALYSIS_DIR", None)
            self.assertTrue(base_server.query_prima_network("Mara").startswith("ERROR: no network"))
            self.assertTrue(base_server.prima_network_overview().startswith("ERROR: no network"))
            report = base_server.build_prima_network()
            self.assertIn("unit: line", report)
            self.assertIn("words: 6 | links: 7", report)  # 3 + 3 + 1 pairs over three lines
            out = base_server.query_prima_network("tower", target="bed")
            self.assertIn("path to bed: tower -> Mara -> Ilse -> bed", out)
            self.assertTrue(base_server.query_prima_network("the").startswith("ERROR"))  # stop word
            self.assertNotIn("prima_network", base_server.fetch_analysis_data())  # not a source
            ov = base_server.prima_network_overview()
            self.assertIn("WORD NETWORK OVERVIEW: 6 words, 7 links", ov)
            self.assertIn("Names (capitalised mid-sentence: people, places): Ilse (2), Mara (2)", ov)


class WizardTest(unittest.TestCase):
    def test_create_wizard_shows_live_state(self):
        import base_server
        with tempfile.TemporaryDirectory() as notes:
            with open(os.path.join(notes, "book.txt"), "w", encoding="utf-8") as f:
                f.write("x " * 400)
            os.environ["PESSOA_ANALYSIS_DIR"] = notes
            self.addCleanup(os.environ.pop, "PESSOA_ANALYSIS_DIR", None)
            text = base_server.create_character()[0].content.text
        self.assertIn("book.txt: ~200 tokens", text)
        self.assertIn("Word network: not built", text)
        self.assertIn("coder", text)              # blueprint list
        self.assertIn("Stack_And_Dagger", text)   # names already taken
        self.assertIn("Start with Step 1", text)

    def test_load_menu_and_direct_load(self):
        import base_server
        menu = base_server.load_character()[0].content.text
        self.assertIn("Stack And Dagger — Every block leaves a body.", menu)
        self.assertIn("get_activation_prompt", menu)
        self.assertIn("Hold this register", base_server.load_character("Stack_And_Dagger")[0].content.text)
        self.assertIn("Hold this register", base_server.get_activation_prompt("Stack_And_Dagger"))
        self.assertTrue(base_server.get_activation_prompt("../etc").startswith("Error"))


def _temp_character(test, name, cabinet_extra=""):
    """A real character in characters/ (gitignored), removed after the test."""
    path = os.path.join(ROOT, "characters", name)
    test.addCleanup(shutil.rmtree, path, True)
    with contextlib.redirect_stdout(io.StringIO()):
        create_heteronym(name, "# engine\n## Shadow\nfear", PROFILE, skin_content="# skin",
                         seed_content="# seed", rules_content="# rules")
    return path


class ValidateTest(unittest.TestCase):
    def test_module_self_test(self):
        from validate import _self_test as v
        self.assertTrue(v())

    def test_shipped_characters_have_no_errors(self):
        from validate import validate_dir
        for name in ("Stack_And_Dagger", "Helena_Vos", "Tomas_Brisa"):
            errors = [m for lvl, m in validate_dir(os.path.join(ROOT, "characters", name)) if lvl == "ERROR"]
            self.assertEqual(errors, [], name)
        # IPIP-NEO facet names are the same facets, not missing ones.
        stack = " ".join(m for _, m in validate_dir(os.path.join(ROOT, "characters", "Stack_And_Dagger")))
        self.assertNotIn("unscored", stack)

    def test_new_cabinet_is_current_schema(self):
        import yaml
        from validate import SCHEMA_VERSION
        path = _temp_character(self, f"Test_Schema_{os.getpid()}")
        with open(os.path.join(path, "ai_cabinet.yaml"), encoding="utf-8") as f:
            self.assertEqual(yaml.safe_load(f)["schema_version"], SCHEMA_VERSION)


class MigrateTest(unittest.TestCase):
    def test_preview_writes_nothing_and_apply_keeps_backup(self):
        import migrate
        name = f"Test_Migrate_{os.getpid()}"
        path = _temp_character(self, name)
        cab = os.path.join(path, "ai_cabinet.yaml")
        with open(cab, encoding="utf-8") as f:
            current = f.read()
        old = current.replace("schema_version: 2\n", "").replace("temperature: 0.6", "temperature: 0.7")
        with open(cab, "w", encoding="utf-8") as f:
            f.write(old)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(migrate.main([name]), 1)
        self.assertIn("+schema_version: 2", out.getvalue())
        with open(cab, encoding="utf-8") as f:
            self.assertEqual(f.read(), old)  # preview only
        with contextlib.redirect_stdout(io.StringIO()):
            migrate.main([name, "--apply"])
        with open(cab, encoding="utf-8") as f:
            self.assertEqual(f.read(), current)
        with open(cab + ".bak", encoding="utf-8") as f:
            self.assertEqual(f.read(), old)


class DryRunTest(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        name = f"Test_Dry_{os.getpid()}"
        # If dry_run ever breaks, the folder is real: clean it up either way.
        self.addCleanup(shutil.rmtree, os.path.join(ROOT, "characters", name), True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            path = hydrate_files({"engine.md": "e", "big_five.json": '{"O": 3}'}, name, dry_run=True)
        self.assertFalse(os.path.exists(path))
        self.assertIn("DRY RUN: nothing written", out.getvalue())
        self.assertIn("ERROR: missing layer: skin.md", out.getvalue())

    def test_real_hydration_saves_facet_summaries_and_checks_quotes(self):
        import json
        name = f"Test_Facets_{os.getpid()}"
        self.addCleanup(shutil.rmtree, os.path.join(ROOT, "characters", name), True)
        with open(os.path.join(ROOT, "templates", "big_five.json"), encoding="utf-8") as f:
            facets = json.load(f)["facets"]
        sheet = {"scale": "0-1",
                 "summary": {"openness": 0.5, "conscientiousness": 0.5, "extraversion": 0.18,
                             "agreeableness": 0.5, "neuroticism": 0.5},
                 "facets": {t: {k: 0.5 for k in fs} for t, fs in facets.items()},
                 "evidence": {"openness": {"intellect": {"reason": "r", "quote": "reads the footnotes"},
                                           "imagination": {"reason": "r", "quote": "dreams in colour"}}}}
        sheet["facets"]["extraversion"]["warmth"] = 0.8
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            path = hydrate_files({"skin.md": "s", "engine.md": "e", "big_five.json": json.dumps(sheet),
                                  "seed.md": "x", "operational_rules.md": "r"}, name,
                                 source_text="She always reads the footnotes.")
        report = out.getvalue()
        self.assertIn("Summary set from facets: extraversion 0.18 -> 0.55", report)
        self.assertIn("Evidence quotes found in the source: 1/2", report)
        self.assertIn("openness.imagination: 'dreams in colour'", report)
        with open(os.path.join(path, "big_five.json"), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved["summary"]["extraversion"], 0.55)
        self.assertEqual(saved["summary_written"], {"extraversion": 0.18})
        from validate import validate_dir
        self.assertFalse([m for _, m in validate_dir(path) if "facet mean" in m])

    def test_cli_source_option_does_not_replace_the_blob(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            blob, src = os.path.join(d, "blob.txt"), os.path.join(d, "src.txt")
            with open(blob, "w", encoding="utf-8") as f:
                f.write("--- FILE: skin.md ---\n- **Name**: Cli Test\n--- FILE: big_five.json ---\n"
                        '{"scale": "1-5", "O": 3, "evidence": {"openness": {"intellect": '
                        '{"reason": "r", "quote": "reads the footnotes"}}}}\n')
            with open(src, "w", encoding="utf-8") as f:
                f.write("She reads the footnotes.")
            r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "hydrate.py"), blob,
                                "--dry-run", "--source", src], capture_output=True, text=True)
        self.assertIn("Evidence quotes found in the source: 1/1", r.stdout, r.stdout + r.stderr)
        self.assertIn("DRY RUN: nothing written", r.stdout)

    def test_folder_hydration_reads_layers(self):
        from hydrate import read_folder
        files = read_folder(os.path.join(ROOT, "characters", "Stack_And_Dagger"))
        self.assertEqual(set(files), {"skin.md", "engine.md", "big_five.json", "seed.md",
                                      "operational_rules.md", "ACTIVATION_PROMPT.md"})

    def test_accented_names_keep_their_letters(self):
        self.assertEqual(sanitize_heteronym_name("Tomás Brisa"), "Tomas_Brisa")
        self.assertEqual(sanitize_heteronym_name("Antonín Dvořák"), "Antonin_Dvorak")


class RuntimeModulesTest(unittest.TestCase):
    def test_self_tests(self):
        import audit, export, memory, state
        for mod in (state, memory, audit, export):
            self.assertTrue(mod._self_test(), mod.__name__)

    def test_state_and_memory_write_without_fchmod(self):
        # Windows with Python < 3.13 has no os.fchmod.
        from unittest import mock
        import memory, state
        with tempfile.TemporaryDirectory() as d, mock.patch.object(os, "fchmod", None):
            self.assertEqual(state.update(d, stress=0.5)["stress"], 0.5)
            memory.remember(d, "Prefers tea", "preference")
            self.assertIn("Prefers tea", memory.recall(d))


class _Session:
    def __init__(self):
        self.notified = 0

    async def send_prompt_list_changed(self):
        self.notified += 1


class _Ctx:
    def __init__(self):
        self.session = _Session()


class ServerFeaturesTest(unittest.TestCase):
    def setUp(self):
        import base_server
        self.s = base_server
        self.name = f"Test_Live_{os.getpid()}"
        self.path = _temp_character(self, self.name)

    def test_server_sends_instructions_and_logs_elsewhere(self):
        s = self.s
        self.assertIn("remember(name, text, kind)", s.mcp.instructions)
        self.assertNotEqual(s.LOG_FILE, os.path.join(ROOT, "pessoa_bridge.log"))

    def test_state_and_memory_reach_the_activation_prompt(self):
        s = self.s
        self.assertNotIn("Current state", s.get_activation_prompt(self.name))
        s.set_character_state(self.name, fatigue=0.8, thread="the tower")
        s.remember(self.name, "User prefers prose to lists", "preference", who="user")
        text = s.get_activation_prompt(self.name)
        self.assertIn("You are tired", text)
        self.assertIn("You are in the middle of: the tower", text)
        self.assertIn("(user)] User prefers prose to lists", text)
        self.assertTrue(text.endswith(s._HOLD_INSTRUCTION))
        self.assertIn("max_tokens", s.get_character_state(self.name))
        self.assertIn("1 entries archived", s.compact_memory(self.name, "Prose, not lists."))
        self.assertTrue(s.remember(self.name, "x", "gossip").startswith("Error"))
        self.assertTrue(s.set_character_state("../etc", fatigue=1).startswith("Error"))

    def test_prompts_follow_the_folder(self):
        import asyncio
        s = self.s
        listed = lambda: {p.name for p in asyncio.run(s.mcp.list_prompts())}  # noqa: E731
        self.assertIn(f"activate_{self.name}", listed())  # made after the server imported
        shutil.rmtree(self.path)
        self.assertNotIn(f"activate_{self.name}", listed())
        ctx = _Ctx()
        blob = ("--- FILE: skin.md ---\n# s\n--- FILE: engine.md ---\ne\n--- FILE: big_five.json ---\n"
                '{"O": 3, "C": 3, "E": 3, "A": 3, "N": 3}\n')
        dry = asyncio.run(s.trigger_identity_hydration(blob, ctx, name=self.name, dry_run=True))
        self.assertIn("DRY RUN", dry)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(ctx.session.notified, 0)
        self.assertIn("SUCCESS", asyncio.run(s.trigger_identity_hydration(blob, ctx, name=self.name)))
        self.assertEqual(ctx.session.notified, 1)
        self.assertIn(f"activate_{self.name}", listed())

    def test_council_handoff_compare_compose_export(self):
        s = self.s
        text = s.council("Helena_Vos, Tomas_Brisa", "Should we ship on Friday?")[0].content.text
        self.assertIn("PROFILE: Helena_Vos", text)
        self.assertIn("Should we ship on Friday?", text)
        # As typed in Claude Desktop's form: spaces, no comma, other case.
        for typed in ("Helena Vos Tomas Brisa", "helena vos and tomas brisa", "Helena Vos; Tomas_Brisa"):
            got = s.council(typed, "x")[0].content.text
            self.assertIn("Members: Helena_Vos, Tomas_Brisa.", got, typed)
        # A bad list returns a message saying why (a raised error shows only
        # "failed to attach prompt" in Claude Desktop) and starts nothing.
        for bad, why in (("Helena_Vos", "needs 2 to 4 different characters, got 1"),
                         ("Helena Vos, Nobody Here", "no character named 'Nobody Here'"),
                         ("Helena Vos, Helena_Vos", "got 1")):
            msg = s.council(bad, "x")[0].content.text
            self.assertIn(why, msg, bad)
            self.assertIn("Do not start the prompt's task", msg)
            self.assertNotIn("PROFILE:", msg)
        self.assertIn("could not run: no character named 'Nobody'", s.activate_heteronym("Nobody")[0].content.text)
        sc = s.scene("Helena Vos Tomas Brisa", "a first date in a coffee shop")[0].content.text
        self.assertIn("characters: Helena Vos, Tomas Brisa.", sc)
        self.assertIn("a first date in a coffee shop", sc)
        self.assertIn("PROFILE: Helena_Vos", sc)
        self.assertIn("I do not play a part: I direct.", sc)
        self.assertNotIn("Synthesis", sc)
        mine = s.scene("Helena Vos, Tomas Brisa", "a bar", my_role="the waiter")[0].content.text
        self.assertIn("I play the waiter. Never write my lines", mine)
        self.assertIn("could not run", s.scene("Helena Vos", "a bar")[0].content.text)
        self.assertIn("Helena Vos", s.audit_character("helena vos")[0].content.text)
        self.assertIn("## Findings from Tomas_Brisa", s.handoff("Helena_Vos", "Tomas_Brisa", "name a city"))
        self.assertIn("| Tomas_Brisa |", s.compare_characters("helena vos tomas brisa"))
        table = s.compare_characters("Helena_Vos, Tomas_Brisa")
        self.assertIn("| Helena_Vos | 2.52 | 4.72 | 1.8 | 3.2 | 1.48 | 0.528", table)
        composite = s.compose_seeds("coder, security_auditor")
        self.assertIn("## PRIMARY: coder", composite)
        self.assertIn("### Security Auditor Synthesis Profile", composite)
        self.assertTrue(s.compose_seeds("coder, nope").startswith("Error"))
        self.assertIn("anthropic.json", s.export_character(self.name))
        report = s.audit_response(self.name, "As an AI language model, my temperature is 0.6.")
        self.assertIn("Law of Register", report)
        self.assertIn("Law of Privacy", report)
        self.assertIn("then call audit_response(name=", s.benchmark_character(self.name)[0].content.text)
        self.assertIn("10 Core Laws", s.validate_character(self.name))

    def test_source_caps(self):
        s = self.s
        with tempfile.TemporaryDirectory() as notes:
            for i in range(3):
                with open(os.path.join(notes, f"n{i}.txt"), "w", encoding="utf-8") as f:
                    f.write("text")
            old = s.MAX_SOURCES
            s.MAX_SOURCES = 2
            self.addCleanup(setattr, s, "MAX_SOURCES", old)
            sources, skipped = s._read_sources(notes)
        self.assertEqual(len(sources), 2)
        self.assertIn("n2.txt (over the 2-file cap, PESSOA_MAX_SOURCES)", skipped)


class CalculusTest(unittest.TestCase):
    def test_documented_bands(self):
        self.assertEqual(_self_test(), [])


if __name__ == "__main__":
    unittest.main()
