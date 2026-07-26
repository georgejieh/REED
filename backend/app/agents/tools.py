"""Tools exposed to the model during an agent run.

REED exposes none. The RSS pre-flight fetches and filters the headlines
before the model is called, so the model already has every story it is
allowed to write about and produces the full JSON brief in one turn.
Keeping the tool list empty is what makes the run single-turn and
predictable: there is no loop to stall, no tool budget to exhaust, and
no way for the model to reach the open web mid-session.

`get_agent_tools` stays as the single seam where tools would be
reintroduced, so the runner and generator never need to change.
"""

from __future__ import annotations

import logging

from app.config import AppConfig
from app.providers.tools import Tool

logger = logging.getLogger(__name__)


def get_agent_tools(config: AppConfig) -> list[Tool]:
    """Return the tools the model gets during an agent run.

    Always empty. See the module docstring for why.
    """
    return []
