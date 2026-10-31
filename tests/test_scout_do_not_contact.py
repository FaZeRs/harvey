"""Scout never adds someone on the do-not-contact list, nor spends lookups on them."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harvey.agents.scout import Scout
from harvey.config import ComplianceConfig
from harvey.models.prospect import Prospect


def _scout(prospect_exists=False):
    config = SimpleNamespace(compliance=ComplianceConfig(do_not_contact=["blocked.example"]),
                             icp=SimpleNamespace(industries=[]))
    state = SimpleNamespace(prospect_exists=AsyncMock(return_value=prospect_exists))
    scout = Scout(SimpleNamespace(), state, config, SimpleNamespace())
    scout._seen_prospect_keys = set()
    return scout


def _prospect(email):
    return Prospect(first_name="Jane", last_name="Doe", email=email, company="Acme")


@pytest.mark.asyncio
async def test_blocked_prospect_reads_as_duplicate():
    scout = _scout()
    assert await scout._is_duplicate_prospect(_prospect("jane@mail.blocked.example"))
    assert not await scout._is_duplicate_prospect(_prospect("jane@acme.example"))


def test_blocked_company_domain():
    scout = _scout()
    assert scout._never_contact(domain="blocked.example") == "blocked.example"
    assert scout._never_contact(domain="notblocked.example") == ""


@pytest.mark.asyncio
async def test_backfill_skips_blocked_company():
    scout = _scout()
    scout.state.companies_needing_prospects = AsyncMock(
        return_value=[{"id": "c1", "domain": "blocked.example", "name": "Blocked"}])
    scout._contacts_from_company = AsyncMock(return_value=[])
    await scout._prospect_via_known_companies()
    scout._contacts_from_company.assert_not_awaited()
