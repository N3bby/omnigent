"""Registers the ``hermes-remote`` harness with Omnigent.

Both the Omnigent server and the host load this module: the server to accept
the harness name and list it in the agent picker, the host to launch it.
It stays light on purpose, since entry points load early in both.
"""
from __future__ import annotations

from omnigent.harness_capabilities import (
    AuthModel,
    EffortFamily,
    Elicitation,
    HarnessCapabilities,
    InstructionDelivery,
    IntegrationMode,
    ModelFamily,
    Resume,
)
from omnigent.harness_plugins import HarnessContribution

HARNESS = "hermes-remote"


def get_contribution() -> HarnessContribution:
    return HarnessContribution(
        name="omnigent-hermes-remote",
        valid_harnesses=frozenset({HARNESS}),
        harness_modules={HARNESS: "omnigent.community.harness.hermes_remote.harness"},
        harness_labels={HARNESS: "Hermes (remote)"},
        # No install spec: the harness needs no CLI on the host, only a
        # reachable Hermes API server, so the host reports it as configured.
        capabilities={
            HARNESS: HarnessCapabilities(
                # Hermes runs its own agent loop behind its API server.
                IntegrationMode.SDK_IN_PROCESS,
                # Hermes asks for approvals itself; see executor.py.
                Elicitation.NONE,
                # The conversation lives in a Hermes session that outlasts
                # the harness process.
                Resume.WARM_REATTACH,
                EffortFamily.NONE,
                ModelFamily.MULTI,
                AuthModel.OWN_AUTH,
                subagents=False,
                interrupt=True,
                streaming=True,
                # Hermes has its own personality and instructions (SOUL.md).
                instruction_delivery=InstructionDelivery.NOT_DELIVERED,
            )
        },
    )
