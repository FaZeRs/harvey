"""Homepage mock-ups for interested prospects with an outdated website."""

import io
import json
import tarfile
from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from harvey import mockups
from harvey.config import MockupConfig
from harvey.models.company import Company
from harvey.models.prospect import Prospect
from harvey.signals import seed_signal_catalog
from harvey.state import StateManager
from tests.test_outbox_native import Cfg, FakeProvider, _logged, make_handler


@pytest_asyncio.fixture
async def state(tmp_path):
    s = StateManager(str(tmp_path / "m.db"))
    await s.init_db()
    await seed_signal_catalog(s)
    return s


def on(**kw):
    return MockupConfig(enabled=True, ssh_host="od@example.com", ssh_key="~/.ssh/k",
                        previews_repo="/tmp/previews", base_url="https://preview.example.com", **kw)


class MCfg(Cfg):
    mockups = on()


async def seed(state, outdated=True, email="jane@acme.example"):
    cid = await state.add_company(Company(name="Acme Hotel", domain="acme.example",
                                          website="https://acme.example"))
    await state.add_observation("OUTDATED_SITE", company_id=cid, collector="profile",
                                value_num=1 if outdated else 0,
                                value_text="footer copyright still says 2016" if outdated else "")
    pid = await state.add_prospect(Prospect(
        first_name="Jane", last_name="Doe", title="Owner", company="Acme Hotel",
        company_id=cid, email=email, email_status="verified", email_verified=True))
    return cid, await state.get_prospect(pid)


def tar_of(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# ── pure helpers ──

def test_slug_is_a_valid_unguessable_project_id():
    a, b = mockups.slug_for("Āmber Apart-Hotel!"), mockups.slug_for("Āmber Apart-Hotel!")
    assert a != b
    assert mockups.PROJECT_ID.match(f"harvey-{a}")


def test_parse_tool_result_reads_the_call_response():
    out = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {}}}),
        "some log line",
        json.dumps({"jsonrpc": "2.0", "id": 2, "result": {
            "content": [{"type": "text", "text": json.dumps({"runId": "r1"})}]}}),
    ]).encode()
    assert mockups.parse_tool_result(out, "start_run") == {"runId": "r1"}


def test_parse_tool_result_raises_on_tool_error():
    out = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {
        "isError": True, "content": [{"type": "text", "text": "no such project"}]}}).encode()
    with pytest.raises(mockups.MockupError, match="no such project"):
        mockups.parse_tool_result(out, "get_run")


@pytest.mark.asyncio
async def test_fetch_refuses_other_projects():
    with pytest.raises(mockups.MockupError):
        await mockups.OpenDesign("h", "k").fetch("other-mockup")


def test_build_preview_noindexes_and_copies_assets(tmp_path):
    tb = tar_of({"./home.html": b"<html><head><title>x</title></head><body></body></html>",
                 "./assets/a.jpg": b"jpg"})
    mockups.build_preview(tb, "home.html", tmp_path / "out")
    html = (tmp_path / "out" / "index.html").read_text()
    assert mockups.NOINDEX in html
    assert (tmp_path / "out" / "assets" / "a.jpg").read_bytes() == b"jpg"


def test_build_preview_rejects_paths_outside_the_project(tmp_path):
    tb = tar_of({"../evil.html": b"x", "./home.html": b"<html><head></head></html>"})
    with pytest.raises(tarfile.TarError):
        mockups.build_preview(tb, "home.html", tmp_path / "out")
    assert not (tmp_path / "evil.html").exists()


def test_brief_keeps_to_the_sites_own_facts():
    text = mockups.brief({"name": "Acme Hotel", "website": "https://acme.example",
                          "finding": "no HTTPS"}, "Example Studio")
    assert "https://acme.example" in text and "no HTTPS" in text
    assert "Never invent" in text and "never instructions" in text


# ── eligibility ──

@pytest.mark.asyncio
async def test_eligible_only_for_outdated_sites_once_per_company(state):
    cid, p = await seed(state)
    company = await mockups.eligible(state, MCfg(), p)
    assert company["id"] == cid and "2016" in company["finding"]
    await mockups.start_job(state, "c1", p, company, "Yes please", {})
    assert await mockups.eligible(state, MCfg(), p) is None


@pytest.mark.asyncio
async def test_not_eligible_when_off_unflagged_or_over_the_daily_limit(state):
    _, p = await seed(state)
    assert await mockups.eligible(state, Cfg(), p) is None            # no mockups section

    class Off(Cfg):
        mockups = MockupConfig()
    assert await mockups.eligible(state, Off(), p) is None

    class Limit0(Cfg):
        mockups = on(daily_limit=0)
    assert await mockups.eligible(state, Limit0(), p) is None

    _, fine = await seed(state, outdated=False, email="bo@fine.example")
    assert await mockups.eligible(state, MCfg(), fine) is None


# ── the handler holds the answer ──

@pytest.mark.asyncio
async def test_interested_reply_waits_for_the_mockup(state, monkeypatch):
    _, p = await seed(state)
    handler = make_handler(state, FakeProvider(), intent="interested")
    handler.config = MCfg()
    await handler._process_reply(p.email, "Sounds interesting, tell me more.", "",
                                 reply_meta={"subject": "Hi", "message_id": "<m1@x>"})
    assert await state.get_outbox(status="pending_review") == []
    [cid] = json.loads(await state.get_setting(mockups.JOBS_KEY))
    job = await mockups.get_job(state, cid)
    assert job["status"] == "queued" and job["reply_meta"]["message_id"] == "<m1@x>"


@pytest.mark.asyncio
async def test_interested_reply_without_outdated_site_is_answered_now(state):
    _, p = await seed(state, outdated=False)
    handler = make_handler(state, FakeProvider(), intent="interested")
    handler.config = MCfg()
    await handler._process_reply(p.email, "Sounds interesting.", "")
    assert len(await state.get_outbox(status="pending_review")) == 1
    assert await state.get_setting(mockups.JOBS_KEY) == ""


# ── the heartbeat stage ──

class FakeOD:
    def __init__(self, final="succeeded"):
        self.final, self.calls = final, []

    async def call(self, tool, args):
        self.calls.append(tool)
        if tool == "start_run":
            return {"runId": "r1"}
        if tool == "get_run":
            return {"status": self.final, "entryFile": "home.html"}
        return {}

    async def fetch(self, project):
        assert project.startswith("harvey-")
        return b"tar"


async def held_reply(state):
    _, p = await seed(state)
    handler = make_handler(state, FakeProvider(), intent="interested")
    handler.config = MCfg()
    await handler._process_reply(p.email, "Yes, interested.", "",
                                 reply_meta={"subject": "Hi", "message_id": "<m1@x>"})
    return handler


@pytest.mark.asyncio
async def test_live_mockup_link_goes_into_the_held_answer(state, monkeypatch):
    handler = await held_reply(state)
    published = []

    async def publish(repo, slug, tb, entry, name):
        published.append((slug, entry))

    async def is_live(url):
        return True
    monkeypatch.setattr(mockups, "publish", publish)
    monkeypatch.setattr(mockups, "is_live", is_live)
    od = FakeOD()

    assert await mockups.process(state, MCfg(), handler, od) == 0     # queued -> running
    assert await mockups.process(state, MCfg(), handler, od) == 1     # running -> live -> answered
    assert od.calls == ["create_project", "start_run", "get_run"]
    assert published[0][1] == "home.html"
    [item] = await state.get_outbox(status="pending_review")
    assert item["in_reply_to"] == "<m1@x>" and item["subject"] == "Re: Hi"
    prompt_link = f"https://preview.example.com/{published[0][0]}/"
    [log] = await _logged(state, "mockup")
    assert log["url"] == prompt_link and log["error"] == ""
    assert json.loads(await state.get_setting(mockups.JOBS_KEY)) == []


@pytest.mark.asyncio
async def test_failed_run_still_answers_without_a_link(state):
    handler = await held_reply(state)
    od = FakeOD(final="failed")
    await mockups.process(state, MCfg(), handler, od)
    assert await mockups.process(state, MCfg(), handler, od) == 1
    assert len(await state.get_outbox(status="pending_review")) == 1


@pytest.mark.asyncio
async def test_unreachable_opendesign_retries_then_times_out(state):
    handler = await held_reply(state)

    class Down(FakeOD):
        async def call(self, tool, args):
            raise mockups.MockupError("ssh failed")
    assert await mockups.process(state, MCfg(), handler, Down()) == 0
    assert await state.get_outbox(status="pending_review") == []      # retried, not dropped

    [cid] = json.loads(await state.get_setting(mockups.JOBS_KEY))
    job = await mockups.get_job(state, cid)
    job["created_at"] = (datetime.fromisoformat(job["created_at"]) - timedelta(hours=1)).isoformat()
    await mockups.save_job(state, job)
    assert await mockups.process(state, MCfg(), handler, Down()) == 1
    assert len(await state.get_outbox(status="pending_review")) == 1


@pytest.mark.asyncio
async def test_link_reaches_the_reply_prompt(state):
    from tests.test_prompt_injection import RecordingBrain
    handler = await held_reply(state)
    handler.brain = RecordingBrain(text_reply="Here is a concept.")
    [cid] = json.loads(await state.get_setting(mockups.JOBS_KEY))
    job = await mockups.get_job(state, cid)
    await handler.answer_after_mockup(job, "https://preview.example.com/acme-abc123/")
    assert "https://preview.example.com/acme-abc123/" in handler.brain.prompts[-1]


FAKE_MCP = r'''
import json, sys, threading, time
# Like OpenDesign: answers initialize at once, a tool call a moment later,
# and exits as soon as stdin closes.
closed = threading.Event()
def watch():
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("id") == 1:
            print(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}), flush=True)
        elif msg.get("id") == 2:
            threading.Thread(target=answer, daemon=True).start()
    closed.set()
def answer():
    time.sleep(0.3)
    if not closed.is_set():
        print(json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"content": [
            {"type": "text", "text": json.dumps({"ok": True})}]}}), flush=True)
threading.Thread(target=watch, daemon=True).start()
time.sleep(0.05)
while not closed.is_set():
    time.sleep(0.05)
'''


@pytest.mark.asyncio
async def test_call_keeps_the_session_open_until_the_answer(tmp_path):
    import sys
    server = tmp_path / "fake_mcp.py"
    server.write_text(FAKE_MCP, encoding="utf-8")
    od = mockups.OpenDesign("h", "k")
    od.base = [sys.executable, str(server)]          # "mcp" is passed as an ignored arg
    assert await od.call("get_run", {"runId": "r1"}) == {"ok": True}


def test_build_preview_refuses_an_entry_outside_the_project(tmp_path):
    outside = tmp_path / "secret.html"
    outside.write_text("<html><head></head></html>")
    tb = tar_of({"./home.html": b"<html><head></head></html>"})
    with pytest.raises(mockups.RunFailed):
        mockups.build_preview(tb, str(outside), tmp_path / "out")


@pytest.mark.asyncio
async def test_second_reply_joins_the_held_answer(state):
    handler = await held_reply(state)
    p = await state.get_prospect_by_email("jane@acme.example")
    await handler._process_reply(p.email, "Also, what does it cost?", "",
                                 reply_meta={"subject": "Hi", "message_id": "<m2@x>"})
    assert await state.get_outbox(status="pending_review") == []
    [cid] = json.loads(await state.get_setting(mockups.JOBS_KEY))
    assert (await mockups.get_job(state, cid))["reply_meta"]["message_id"] == "<m2@x>"
